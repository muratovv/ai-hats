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
#   task-merge      -> the merge's own checkout, not the payload cwd: peel
#                      `git -C` and a leading `cd` (shell_walk). Refuse when
#                      THAT HEAD is master/main. A target that cannot be read
#                      is a refuse — guessing would let the merge through.
#                      task→task stays allowed.
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

# Directory the task-branch `git merge` actually runs in, or empty when the
# line cannot be read (a lost `cd`, a bare `git -C`). Empty is a refuse.
# Inlined rather than imported: a materialized hook does not sit next to shell_walk.
merge_checkout() {
    local cwd="$1" line
    line="$(
        TASK_MERGE_CMD="$cmd" TASK_MERGE_CWD="$cwd" python3 - <<'PY' || true
import os
import re
import shlex
from pathlib import Path

SEPARATORS = ("&&", "||", ";", "|", "&")
TASK_REF = re.compile(r"^task/[A-Za-z]+-[0-9]+$")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def segments(words):
    out = [[]]
    for word in words:
        if word in SEPARATORS:
            out.append([])
        else:
            out[-1].append(word)
    return [seg for seg in out if seg]


def strip_prefix(segment):
    i = 0
    while i < len(segment):
        word = segment[i]
        if word == "env" or ENV_ASSIGN.match(word):
            i += 1
            continue
        break
    return segment[i:]


def peel(args, cwd):
    i = 0
    while i < len(args):
        if args[i] == "-C":
            if i + 1 >= len(args):
                return None
            try:
                cwd = (cwd / args[i + 1]).resolve()
            except (OSError, ValueError):
                return None
            i += 2
            continue
        if args[i] == "-c" and i + 1 < len(args):
            i += 2
            continue
        if args[i].startswith("-"):
            i += 1
            continue
        break
    return cwd, args[i:]


try:
    words = tokens(os.environ["TASK_MERGE_CMD"])
except ValueError:
    print("UNRESOLVED")
    raise SystemExit(0)

effective = Path(os.environ["TASK_MERGE_CWD"])
for raw in segments(words):
    segment = strip_prefix(raw)
    if not segment:
        continue
    if segment[0] == "cd" and len(segment) > 1:
        try:
            effective = (effective / segment[1]).resolve()
        except (OSError, ValueError):
            print("UNRESOLVED")
            raise SystemExit(0)
        continue
    if Path(segment[0]).name != "git" or len(segment) < 2:
        continue
    peeled = peel(segment[1:], effective)
    if peeled is None:
        print("UNRESOLVED")
        raise SystemExit(0)
    target, args = peeled
    if args and args[0] == "merge" and any(TASK_REF.match(arg) for arg in args[1:]):
        print("DIR\t" + str(target))
        raise SystemExit(0)
print("NONE")
PY
    )"
    case "$line" in
        DIR$'\t'*) printf '%s' "${line#DIR$'\t'}" ;;
        *) printf '' ;;
    esac
}

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
        # The payload cwd is where the agent stands, not where the merge lands.
        cwd="$(extract_field '.cwd // empty' 'data.get("cwd")')"
        cwd="${cwd:-$PWD}"
        target="$(merge_checkout "$cwd")"
        if [[ -z "$target" ]]; then
            : # unreadable command — refuse below rather than guess
        else
            branch="$(git -C "$target" symbolic-ref --short HEAD 2>/dev/null || true)"
            case "$branch" in
                master|main)
                    : # refuse below
                    ;;
                "")
                    # Resolved, but not a branch (no repo, detached). The merge
                    # cannot update master; allow with a breadcrumb.
                    echo "[task-merge-guard] no branch at $target — allowing" >&2
                    exit 0
                    ;;
                *)
                    exit 0
                    ;;
            esac
        fi
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
