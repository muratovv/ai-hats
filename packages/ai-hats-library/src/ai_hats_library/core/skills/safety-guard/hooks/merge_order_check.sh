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
#   *->review    — may this card ENTER review? Only with a live review diff:
#                  the task branch must not already be contained in the base
#                  branch. Catches merges that happened by any road, including
#                  ones predating the pre-merge guard.
#
# D2 verdict contract (hook_exec._classify): exit 0 = pass, exit 2 = refuse
# with the reason ON STDOUT (a refusal spoken on stdout stands alone), any
# other non-zero = broke — and the points above are fail-closed (ADR-0019 D4),
# so broke aborts too. That is deliberate: a check that cannot verify the
# review surface must not wave the merge through.
#
# Env the channel hands in (hook_exec._hook_env + wt extra_env):
#   AI_HATS_HOOK_POINT, AI_HATS_PROJECT_DIR, AI_HATS_TASK_ID (rack channel),
#   AI_HATS_BRANCH_NAME (wt channel).

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

    # The base is what `wt merge` lands into (HATS-942): the checkout's current
    # HEAD branch, master/main as the fallback for a detached HEAD. A repo with
    # no nameable base gets no verdict here — the pre-merge state check above
    # holds the primary invariant and does not depend on the base's name.
    base="$(git -C "$project_dir" symbolic-ref --short HEAD 2>/dev/null || true)"
    if [[ -z "$base" ]]; then
        for candidate in master main; do
            if git -C "$project_dir" show-ref --verify --quiet "refs/heads/$candidate"; then
                base="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$base" ]]; then
        echo "merge-order check: no base branch nameable in $project_dir — the containment test does not apply" >&2
        exit 0
    fi

    if ! git -C "$project_dir" show-ref --verify --quiet "refs/heads/$branch"; then
        # No task branch at all — nothing to blind (docs-only card, etc.).
        exit 0
    fi
    if [[ "$branch" != "$base" ]] && git -C "$project_dir" merge-base --is-ancestor "$branch" "$base"; then
        refuse "$branch is already fully contained in $base — the review diff vs merge-base is empty by construction (PROX-619). The card cannot enter review blind; un-merge (or rebase the branch onto $base with new changes) first."
    fi
    exit 0
fi

refuse "unknown hook point '$point' — this check is bound at wt:pre-merge and ->review; anywhere else is a misconfigured binding (fail-closed)."
