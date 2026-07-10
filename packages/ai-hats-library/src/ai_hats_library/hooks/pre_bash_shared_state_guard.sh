#!/usr/bin/env bash
# HATS-437 — Claude Code PreToolUse hook: pause-before-shared-state-write.
#
# Wired into .claude/settings.json by ClaudeProvider.ensure_runtime_hooks()
# (matcher: Bash). On every Bash invocation the hook reads the tool-input
# JSON from stdin, classifies the command via shared_state_classifier.sh,
# and blocks with exit 2 when the command is irreversible AND we are
# running without a controlling TTY (i.e. agent context).
#
# Levels of intervention:
#   classification == safe          -> exit 0 (allow)
#   classification == shared        -> exit 0 (allow; Level 2 rule covers it)
#   classification == irreversible  -> require ack:
#       - TTY available             -> prompt y/N
#       - non-TTY                   -> deny (exit 2)
#       - AI_HATS_SHARED_STATE_ACK=1-> allow with stderr breadcrumb
#
# Per-invocation override (consistent with HATS-402 AI_HATS_ATTACH_ACK):
#     AI_HATS_SHARED_STATE_ACK=1 <agent command>
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
    irreversible)
        : # fallthrough to gating below
        ;;
    *)
        echo "[shared-state-guard] unknown classifier verdict '$verdict' — allowing" >&2
        exit 0
        ;;
esac

# --- 4. Irreversible: gate on ack ---------------------------------------
if [[ "${AI_HATS_SHARED_STATE_ACK:-}" == "1" ]]; then
    echo "[shared-state-guard] AI_HATS_SHARED_STATE_ACK=1 — allowing irreversible: $cmd" >&2
    exit 0
fi

# Interactive path: only when stdin/stderr are TTYs AND we are not in an
# agent harness. Claude Code PreToolUse hooks run with stdin piped (JSON
# payload), so stdin is never a TTY here in practice — but we keep the
# branch for direct CLI testing.
if [[ -t 0 && -t 2 ]]; then
    read -r -p "[shared-state-guard] Irreversible command: $cmd
Proceed? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES) exit 0 ;;
        *)           exit 2 ;;
    esac
fi

# --- 5. Non-TTY: deny ---------------------------------------------------
cat >&2 <<EOF
[shared-state-guard] BLOCKED — irreversible operation requires explicit ack.
  command: $cmd

This command writes shared state with no undo path (HATS-437):
  - gh pr merge ...           (PR + master commit; no undo)
  - git push --force / -f     (overwrites remote history)

Recover without wasting turns (rule_pause_before_shared_state_write):
  1. Do NOT retry, rephrase, or wrap this command — the block is deliberate,
     not a transient error, and will deny again.
  2. In your NEXT turn, show the user the exact command and ask for explicit
     go-ahead. Do not act in the same turn that announces it.
  3. Only after the user confirms, re-run the SINGLE command with the ack
     prefix (do not chain it with &&, ||, ;, | — one shared-state write per call):
       AI_HATS_SHARED_STATE_ACK=1 <command>
EOF
exit 2
