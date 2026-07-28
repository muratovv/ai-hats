#!/usr/bin/env bash
# HATS-437 — Claude Code PreToolUse hook: pause-before-shared-state-write.
#
# Wired into .claude/settings.json by ClaudeProvider.ensure_runtime_hooks()
# (matcher: Bash). On every Bash invocation the hook reads the tool-input
# JSON from stdin, classifies the command via shared_state_classifier.sh,
# and escalates the risky ones to the user.
#
# Levels of intervention:
#   classification == safe          -> exit 0 (allow)
#   classification == shared        -> exit 0 (allow; Level 2 rule covers it)
#   classification == gated
#                  | irreversible   -> exit 0 + permissionDecision "ask":
#                                        the harness prompts the user, and
#                                        blocks when nobody can answer
#                                        (headless -p, cron, CI)
#       - AI_HATS_SHARED_STATE_ACK=1-> allow with stderr breadcrumb
#
# The ack is read from THIS PROCESS'S environment, so it is set where Claude
# Code is launched — the `env` block of .claude/settings.json, or an export in
# the launching shell. It CANNOT be given as a prefix on the agent's command
# (`AI_HATS_SHARED_STATE_ACK=1 git push …`): this hook runs before that command
# exists as a process, so the assignment never reaches us. The hook used to
# advertise exactly that form and then deny it (HATS-1294) — hence "ask", which
# routes consent through the user instead of through a string the agent writes.
#
# The hook does NOT crash the agent on classifier errors or missing jq —
# safe defaults favour the user's flow: when we cannot classify, we allow
# and emit a stderr warning. The Level 2 rule remains the primary line.

set -uo pipefail

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFIER="${HOOK_DIR}/shared_state_classifier.sh"

# --- 1. Read tool-input JSON from stdin --------------------------------
payload="$(cat || true)"
if [[ -z "$payload" ]]; then
    # No payload — likely manual test or harness-level no-op. Allow.
    exit 0
fi

# --- 2. Extract the command field --------------------------------------
# Prefer jq when present (robust); fall back to a python one-liner.
extract_command() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.tool_input.command // empty' <<<"$payload"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print((data.get("tool_input") or {}).get("command") or "")
' <<<"$payload"
        return
    fi
    # No json parser — allow (safe default).
    echo ""
}

cmd="$(extract_command)"
if [[ -z "$cmd" ]]; then
    # Not a Bash invocation or unparsable payload — allow.
    exit 0
fi

# --- 3. Classify -------------------------------------------------------
if [[ ! -f "$CLASSIFIER" ]]; then
    # Classifier missing — emit a stderr breadcrumb and allow. The Level 2
    # rule still warns the agent; we refuse to break the user's flow.
    echo "[shared-state-guard] classifier not found at $CLASSIFIER — allowing" >&2
    exit 0
fi

# shellcheck source=shared_state_classifier.sh
source "$CLASSIFIER"
verdict="$(classify_command "$cmd")"

case "$verdict" in
    safe|shared)
        # Level 2 rule covers `shared`; hook does not interrupt.
        exit 0
        ;;
    gated|irreversible)
        : # fallthrough to gating below
        ;;
    *)
        echo "[shared-state-guard] unknown classifier verdict '$verdict' — allowing" >&2
        exit 0
        ;;
esac

# --- 4. Gate on ack ------------------------------------------------------
if [[ "${AI_HATS_SHARED_STATE_ACK:-}" == "1" ]]; then
    echo "[shared-state-guard] AI_HATS_SHARED_STATE_ACK=1 — allowing $verdict: $cmd" >&2
    exit 0
fi

# --- 5. Escalate to the user through the harness -------------------------
# `permissionDecision: ask` hands the call to the harness: it prompts the user
# interactively, and BLOCKS when nobody can answer (headless `-p`, cron, CI).
# Measured on Claude Code 2.1.220 (HATS-1294 S1) rather than assumed — neither
# `--allowedTools Bash` nor `--permission-mode bypassPermissions` defeats it.
# So the harness owns the interactive/headless split and this hook does not
# detect which one it is in.
#
# The exit code MUST be 0: Claude Code discards a hook's stdout when it exits 2,
# so the JSON below would never be read. (The old code exited 2, which is why it
# could only ever hard-deny.)
if [[ "$verdict" == "gated" ]]; then
    reason="Updates a shared branch others build on (HATS-1253): ${cmd}"
else
    reason="Irreversible — no undo path (HATS-437): ${cmd}
Force-push overwrites remote history; \`gh pr merge\` lands a commit on the default branch."
fi

reason="${reason}

Approve only if this exact command is what you intended.
Agent: this is the user's call to make — do not retry, rephrase or re-issue it.
To pre-approve for a whole session, set AI_HATS_SHARED_STATE_ACK=1 in the
environment that launches Claude Code (e.g. the \`env\` block of
.claude/settings.json). A prefix on the command itself cannot work: this hook
runs before the command exists as a process."

emit_ask() {
    if command -v jq >/dev/null 2>&1; then
        jq -n --arg r "$reason" \
            '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        REASON="$reason" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": os.environ["REASON"],
}}))'
        return
    fi
    # No JSON tool: fall back to the hard deny rather than allowing through.
    echo "[shared-state-guard] BLOCKED — $verdict, and no jq/python3 to request approval." >&2
    echo "  command: $cmd" >&2
    exit 2
}

emit_ask
exit 0
