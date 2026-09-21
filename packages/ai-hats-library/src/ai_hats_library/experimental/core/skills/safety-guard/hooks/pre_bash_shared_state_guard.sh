#!/usr/bin/env bash
# HATS-437 — pre-Bash guard: pause-before-shared-state-write.
#
# Reads a tool-input JSON payload on stdin, classifies the command via
# shared_state_classifier.sh, and refuses the risky ones. Consumed by more than
# one surface — Claude wires it as a PreToolUse hook via
# ClaudeProvider.ensure_runtime_hooks() (matcher: Bash); the cline surface and
# direct CLI probes invoke it as a plain script — so the refusal is emitted in
# whichever dialect the caller speaks (HATS-1294).
#
# Levels of intervention:
#   classification == safe          -> exit 0 (allow)
#   classification == shared        -> exit 0 (allow; Level 2 rule covers it)
#   classification == gated
#                  | irreversible   -> refuse, per caller:
#       payload has hook_event_name -> exit 0 + permissionDecision "ask"
#                                      (harness prompts the user; blocks when
#                                       nobody can answer — headless, cron, CI)
#       otherwise                   -> exit 2 + BLOCKED on stderr
#       AI_HATS_SHARED_STATE_ACK=1  -> allow with stderr breadcrumb
#
# The ack is read from THIS PROCESS'S environment, so it is set wherever the
# agent is launched (a provider's settings `env` block, or an export in the
# launching shell). It CANNOT be given as a prefix on the agent's command
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

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# shellcheck source=bypass_journal.sh
if ! . "${HOOK_DIR}/bypass_journal.sh" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

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

# Which protocol is the caller speaking? Claude Code's PreToolUse payload carries
# `hook_event_name`; a plain `bash guard.sh < payload` (cline's surface test, a
# direct CLI probe, any future harness) does not. We answer in the caller's own
# dialect — see the decision section at the bottom.
extract_hook_event() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.hook_event_name // empty' <<<"$payload"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print(data.get("hook_event_name") or "")
' <<<"$payload"
        return
    fi
    echo ""
}
hook_event="$(extract_hook_event)"

extract_session_id() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.session_id // .sessionId // empty' <<<"$payload"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print(data.get("session_id") or data.get("sessionId") or "")
' <<<"$payload"
        return
    fi
    echo ""
}
session_id="$(extract_session_id)"

# --- 3. Classify -------------------------------------------------------
if [[ ! -f "$CLASSIFIER" ]]; then
    # Classifier missing — emit a stderr breadcrumb and allow. The Level 2
    # rule still warns the agent; we refuse to break the user's flow.
    echo "[shared-state-guard] classifier not found at $CLASSIFIER — allowing" >&2
    ai_hats_journal_bypass fail_open "classifier not found" "$cmd" "$session_id"
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
    ai_hats_journal_bypass hatch AI_HATS_SHARED_STATE_ACK "$cmd" "$session_id"
    exit 0
fi

# --- 5. Refuse, in the caller's own dialect ------------------------------
# Two audiences, two protocols. Claude Code understands `permissionDecision:
# "ask"`, which hands the call to the user: it prompts interactively and BLOCKS
# when nobody can answer (headless `-p`, cron, CI). Measured on 2.1.220
# (HATS-1294 S1), not assumed — neither `--allowedTools Bash` nor
# `--permission-mode bypassPermissions` defeats it.
#
# Every other caller — the cline surface, a direct `bash guard.sh < payload`,
# any future harness — gets the universal convention it already expects:
# `exit 2` with a BLOCKED message on stderr. Emitting Claude's JSON at them
# would be an unparsed blob on stdout and a silently ALLOWED command, so the
# dialect is chosen by what the payload declares, never assumed.
# Verb phrases: both are consumed as "This command <headline>". Keep them so —
# and mind that `${var^}`-style case folding is bash 4+, while macOS still ships
# bash 3.2 at /bin/bash, which a restricted PATH will select.
if [[ "$verdict" == "gated" ]]; then
    headline="updates a shared branch others build on (HATS-1253)"
else
    headline="is irreversible and has no undo path (HATS-437)"
fi

# Consent channels, in the order a reader should try them. Deliberately does NOT
# name a provider-specific file: this text is read under every harness.
consent="Consent reaches this hook in exactly two ways, and neither is available
to the agent — that is the point:
  1. The user approves the prompt this hook raises (where the harness supports
     an interactive decision).
  2. AI_HATS_SHARED_STATE_ACK=1 is present in the environment that launched the
     agent, pre-approving the session.
Prefixing the assignment onto the agent's own command does nothing: this hook
runs before that command exists as a process, so it never reaches us."

reason="This command ${headline}:
  ${cmd}

Approve only if this exact command is what you intended.
Agent: this is the user's call to make — do not retry, rephrase or re-issue it.

${consent}"

deny_hard() {
    {
        echo "[shared-state-guard] BLOCKED — this command ${headline}."
        echo "  command: $cmd"
        echo
        echo "$consent"
    } >&2
    exit 2
}

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
    deny_hard
}

# `hook_event_name` is Claude Code's marker; its absence means the caller does
# not speak that protocol, so refuse the way it understands.
# Recorded HERE, not inside the emitters: emit_ask falls back to deny_hard when
# no JSON tool exists, and one command must not book two catches. No session
# argument on purpose — `$session_id` is the PROVIDER's UUID from the payload and
# would outrank the ai-hats id the hook's own path carries.
if [[ "$hook_event" == "PreToolUse" ]]; then
    ai_hats_journal_catch rule_pause_before_shared_state_write ask "$cmd"
    emit_ask
    exit 0
fi
ai_hats_journal_catch rule_pause_before_shared_state_write deny "$cmd"
deny_hard
