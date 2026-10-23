#!/usr/bin/env bash
# HATS-2062 — merge-order check, bound at wt:pre-merge and rack's ->review edge.
#
# One script, two questions, dispatched by AI_HATS_HOOK_POINT:
#
#   *pre-merge   — may this branch merge NOW? The card behind the branch must
#                  be on the review road (state review|done): an earlier merge
#                  blinds the review diff (merge-base becomes the branch tip,
#                  PROX-619) and skips the reviewer. The supervisor's consent
#                  covers "may I merge", this check covers "is it time".
#   *->review    — may this card ENTER review? Not when the branch was already
#                  --no-ff merged into the base recorded at worktree create
#                  (original_branch), not whatever HEAD is now. That parent is
#                  what empties the review diff. A branch with no commit is not
#                  a merge. A fast-forward leaves no parent and stays a residual.
#
# D2 verdict contract (hook_exec._classify): exit 0 = pass, exit 2 = refuse
# with the reason ON STDOUT (a refusal spoken on stdout stands alone), any
# other non-zero = broke — and the points above are fail-closed (ADR-0019 D4),
# so broke aborts too. That is deliberate: a check that cannot verify the
# review surface must not wave the merge through.
#
# Env the channel hands in (hook_exec._hook_env + wt extra_env):
#   AI_HATS_HOOK_POINT, AI_HATS_PROJECT_DIR, AI_HATS_TASK_ID (rack channel),
#   AI_HATS_TASKS_DIR (where the recorded base lives), AI_HATS_BRANCH_NAME.

set -uo pipefail

point="${AI_HATS_HOOK_POINT:-}"
project_dir="${AI_HATS_PROJECT_DIR:-$PWD}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=bypass_journal.sh
if ! . "${SCRIPT_DIR}/bypass_journal.sh" 2>/dev/null; then
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

refuse() {
    # stdout, not stderr — the channel reads the verdict from there.
    echo "merge-order check: REFUSED — $1"
    ai_hats_journal_catch merge-order "$point" "$1"
    exit 2
}

# ID from a task-branch name: task/<prefix>-<number> → <PREFIX>-<number>.
# Anchored on the task/ prefix — a `release-2` branch is not a card.
# Empty when the branch is not a task branch.
task_id_from_branch() {
    local branch="$1" id
    id="$(sed -n 's|^task/\([A-Za-z]*-[0-9]*\)$|\1|p' <<<"$branch")"
    tr '[:lower:]' '[:upper:]' <<<"$id"
}

card_state() {
    # .task.state via rack, or empty on any failure (the caller fails closed).
    local id="$1" out
    command -v rack >/dev/null 2>&1 || return 0
    if command -v jq >/dev/null 2>&1; then
        out="$(cd "$project_dir" && rack context "$id" --json 2>/dev/null | jq -r '.task.state // empty')" || out=""
    elif command -v python3 >/dev/null 2>&1; then
        out="$(cd "$project_dir" && rack context "$id" --json 2>/dev/null | python3 -c '
import json, sys
try:
    print((json.loads(sys.stdin.read()).get("task") or {}).get("state") or "")
except Exception:
    pass
')" || out=""
    fi
    echo "$out"
}

# original_branch from the worktree state `wt merge` itself reads, or empty.
recorded_base() {
    local key hats state out
    key="${branch//\//-}"
    if [[ -n "${AI_HATS_TASKS_DIR:-}" ]]; then
        hats="$(dirname -- "$AI_HATS_TASKS_DIR")"
        hats="$(dirname -- "$hats")"
        hats="$(dirname -- "$hats")"
        state="$hats/sessions/worktrees/${key}.json"
    else
        state="$project_dir/.agent/ai-hats/sessions/worktrees/${key}.json"
    fi
    [[ -f "$state" ]] || return 0
    if command -v jq >/dev/null 2>&1; then
        out="$(jq -r '.original_branch // empty' "$state" 2>/dev/null)" || out=""
    elif command -v python3 >/dev/null 2>&1; then
        out="$(STATE="$state" python3 -c '
import json, os
try:
    print(json.load(open(os.environ["STATE"])).get("original_branch") or "")
except Exception:
    pass
')" || out=""
    else
        out=""
    fi
    printf '%s' "$out"
}

# --- wt:pre-merge ----------------------------------------------------------
if [[ "$point" == *pre-merge* ]]; then
    branch="${AI_HATS_BRANCH_NAME:-}"
    id="$(task_id_from_branch "$branch")"
    if [[ -z "$id" ]]; then
        # Not a task branch — this check guards task merges only.
        exit 0
    fi
    state="$(card_state "$id")"
    case "$state" in
        review|done)
            exit 0
            ;;
        "")
            refuse "cannot read card $id's state (rack missing or lookup failed) — a merge whose timing cannot be verified does not happen (fail-closed, ADR-0019 D4)."
            ;;
        *)
            refuse "card $id is in '$state' — merging $branch into the base branch is only open on the review road (review → done). A merge now would blind the supervisor's review diff: merge-base becomes the branch tip and the diff is empty by construction (PROX-619)."
            ;;
    esac
fi

# --- rack ->review ---------------------------------------------------------
if [[ "$point" == *'->review' ]]; then
    id="${AI_HATS_TASK_ID:-}"
    [[ -n "$id" ]] || refuse "no AI_HATS_TASK_ID in the hook envelope — cannot verify the review diff is alive (fail-closed)."
    branch="task/$(tr '[:upper:]' '[:lower:]' <<<"$id")"

    # A non-git project has no merge mechanics at all — nothing to blind.
    git -C "$project_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

    if ! git -C "$project_dir" show-ref --verify --quiet "refs/heads/$branch"; then
        # No task branch at all — nothing to blind (docs-only card, etc.).
        exit 0
    fi

    # What `wt merge` lands into is the base captured at create, not the
    # checkout's HEAD now — HEAD wanders, and the diff is against that base.
    base="$(recorded_base)"
    if [[ -z "$base" ]] || ! git -C "$project_dir" show-ref --verify --quiet "refs/heads/$base"; then
        refuse "cannot read the base branch recorded for $branch (worktree state missing or its original_branch is not a branch) — a review whose diff cannot be verified does not open (fail-closed)."
    fi
    # "Was merged" ≠ "is contained": a branch that never carried work is also
    # contained in the base, and such cards (investigations, .agent-local docs)
    # legitimately enter review with an empty diff. A --no-ff merge (what
    # `wt merge` does) leaves the tip as a non-first parent of a merge on the
    # base. Read the parents first: grep -q under pipefail exits the producer
    # with SIGPIPE and the match is then reported as a miss.
    tip="$(git -C "$project_dir" rev-parse "refs/heads/$branch")"
    merged_parents="$(git -C "$project_dir" log --merges --format=%P "$base")"
    if grep -q "[[:space:]]${tip}" <<<"$merged_parents"; then
        refuse "$branch was already merged into $base — the review diff vs merge-base is empty by construction (PROX-619). The card cannot enter review blind; un-merge (or rebase the branch onto $base with new changes) first."
    fi
    exit 0
fi

refuse "unknown hook point '$point' — this check is bound at wt:pre-merge and ->review; anywhere else is a misconfigured binding (fail-closed)."
