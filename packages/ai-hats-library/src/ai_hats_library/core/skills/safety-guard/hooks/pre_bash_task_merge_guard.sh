#!/usr/bin/env bash
# HATS-2062 — pre-Bash guard: no raw merges of task branches into a base branch.
#
# Reads a tool-input JSON payload on stdin, classifies the command via
# task_merge_classifier.sh, and REFUSES a raw `git merge` of a task branch into
# master/main — the spelling that blinded the supervisor's review diff in
# PROX-619 (wtr computes the diff against the merge-base; after the merge the
# base fully contains the branch, so merge-base == branch HEAD and the diff is
# empty by construction).
#
# The refusal is a HARD deny in every dialect — no ack flag, no ask. The
# sanctioned path is the managed one:
#
#     ai-hats wt merge
#
# which carries the card-state check at wt:pre-merge (merge only on the
# review→done road) and the supervisor's consent — an early merge approved
# there is a recorded human decision, not an agent's string.
#
# Verdict handling:
#   safe            -> exit 0 (allow)
#   task-merge      -> resolve the cwd's current branch (one read-only
#                      `git symbolic-ref`); refuse on master/main, allow
#                      otherwise (task→task merges keep the review diff alive)
#   task-merge-base -> the command itself switches to a base branch first;
#                      refuse without any git call
#
# The hook does NOT crash the agent on classifier errors, a missing jq, or a
# cwd that is not a git repo — safe defaults favour the user's flow: when we
# cannot classify, we allow and emit a stderr warning (the merge would fail
# there anyway).

set -uo pipefail

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLASSIFIER="${HOOK_DIR}/task_merge_classifier.sh"

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

# --- 2. Extract the command (and cwd) ----------------------------------
# Prefer jq when present (robust); fall back to a python one-liner.
extract_field() {
    local jq_expr="$1" py_expr="$2"
    if command -v jq >/dev/null 2>&1; then
        jq -r "$jq_expr" <<<"$payload"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        PY_EXPR="$py_expr" python3 -c '
import json, os, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print(eval(os.environ["PY_EXPR"]) or "")
' <<<"$payload"
        return
    fi
    # No json parser — allow (safe default).
    echo ""
}

cmd="$(extract_field '.tool_input.command // empty' '(data.get("tool_input") or {}).get("command")')"
if [[ -z "$cmd" ]]; then
    # Not a Bash invocation or unparsable payload — allow.
    exit 0
fi

# --- 3. Classify -------------------------------------------------------
if [[ ! -f "$CLASSIFIER" ]]; then
    echo "[task-merge-guard] classifier not found at $CLASSIFIER — allowing" >&2
    ai_hats_journal_bypass fail_open "classifier not found" "$cmd" ""
    exit 0
fi

# shellcheck source=task_merge_classifier.sh
source "$CLASSIFIER"
verdict="$(classify_task_merge "$cmd")"

case "$verdict" in
    safe)
        exit 0
        ;;
    task-merge)
        # The command names no target branch — resolve the cwd's. The payload's
        # `cwd` key is where the harness will run it; fall back to our PWD.
        cwd="$(extract_field '.cwd // empty' 'data.get("cwd")')"
        cwd="${cwd:-$PWD}"
        branch="$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null || true)"
        case "$branch" in
            master|main)
                : # refuse below
                ;;
            "")
                # Not a repo / detached / git missing — the merge would fail
                # anyway; allow with a breadcrumb.
                echo "[task-merge-guard] cannot resolve branch in $cwd — allowing" >&2
                exit 0
                ;;
            *)
                # Merging a task branch into a non-base branch keeps the
                # review diff intact — allow.
                exit 0
                ;;
        esac
        ;;
    task-merge-base)
        : # refuse below
        ;;
    *)
        echo "[task-merge-guard] unknown classifier verdict '$verdict' — allowing" >&2
        exit 0
        ;;
esac

# --- 4. Refuse, hard, in the caller's dialect ---------------------------
headline="merges a task branch into a base branch outside the review flow (HATS-2062)"

reason="This command ${headline}:
  ${cmd}

A raw merge before review empties the supervisor's review diff (merge-base
becomes the branch tip) and skips both the card-state check and the
supervisor's consent.

Use the managed path instead — it asks the supervisor and verifies the card is
on the review road:
  ai-hats wt merge

Agent: do not retry, rephrase or re-issue this merge. If the supervisor wants
this branch merged now, they approve it through the managed path."

deny_hard() {
    {
        echo "[task-merge-guard] BLOCKED — this command ${headline}."
        echo "  command: $cmd"
        echo
        echo "$reason"
    } >&2
    exit 2
}

hook_event="$(extract_field '.hook_event_name // empty' 'data.get("hook_event_name")')"

emit_deny() {
    if command -v jq >/dev/null 2>&1; then
        jq -n --arg r "$reason" \
            '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        REASON="$reason" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": os.environ["REASON"],
}}))'
        return
    fi
    deny_hard
}

ai_hats_journal_catch hats-2062-task-merge deny "$cmd"
if [[ "$hook_event" == "PreToolUse" ]]; then
    emit_deny
    exit 0
fi
deny_hard
