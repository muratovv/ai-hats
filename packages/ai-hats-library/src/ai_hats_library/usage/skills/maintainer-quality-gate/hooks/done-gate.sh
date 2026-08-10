#!/usr/bin/env bash
# HATS-1137 / HATS-1540 — the quality gate, bound to BOTH roads into master:
# `edge:review--done` (the FSM automerge) and `wt:pre-merge` (a direct
# `ai-hats wt merge`). One file, no branch between them: the tree under
# judgement arrives as AI_HATS_WORKTREE_PATH at either point.
#
# Two modes:
#
#   --check (DEFAULT — what the `composition.checks` binding runs). Take the
#     given worktree's branch tip and require a green marker for that exact
#     commit. Instant: a few `git rev-parse` and one file read, because it runs
#     INSIDE the per-task rack lock (priority 15, budget 20s vs a 30s lock).
#     The suite itself can never run here — it takes minutes.
#
#   --run (`make done-gate`). Run `scripts/ci-local.sh done-gate` and, on green
#     AND a clean tree, write the marker for HEAD.
#
# A SEPARATE script from pre-push-e2e-master.sh on purpose. That one's default
# mode reads git's pre-push protocol from stdin, and the check runner gives its
# children stdin=DEVNULL — empty stdin hits its "no master target" fast path and
# it exits 0. Reusing it would have produced a gate that is silently always green.
#
# Exit contract (ADR-0020 D2): 0 pass, 2 refuse (the stdout tail becomes the
# reason the agent reads), 126/127 corrupt, EVERYTHING ELSE — including 1 — is
# "the gate broke". Hence `set -uo pipefail` and no `-e`: under `-e` any stray
# non-zero command becomes exit 1, which would report a broken gate where there
# was only a failed `grep`. Every exit below is deliberate.
#
# The check must never invoke a mutating `rack` / `ai-hats wt` command on its own
# task: it runs inside that task's lock, and a subprocess re-entering it would
# deadlock. AI_HATS_IN_HOOK=1 marks that. It only reads.

set -uo pipefail

GATE_NAME='done-gate'
RUN_CMD='make done-gate'

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate-marker.sh"; then
    printf 'done-gate: cannot load %s/../lib/gate-marker.sh — the gate cannot look\n' "$_self_dir"
    printf 'up any marker, so it cannot let this transition through.\n'
    exit 2
fi

# --- resolving the project's own layout ------------------------------------

# This project's OWN tasks dir, from `ai-hats.yaml` and the documented default —
# deliberately NOT from AI_HATS_DIR. That variable is the leaky one: a value
# inherited from another checkout points at a tracker of its own, and the whole
# question below is which tracker this transition belongs to.
own_tasks_dir() {
    local project_dir="$1" configured
    configured="$(sed -n "s/^ai_hats_dir:[[:space:]]*[\"']\{0,1\}\([^\"'[:space:]]*\).*/\1/p" \
                      "$project_dir/ai-hats.yaml" 2>/dev/null | head -1)"
    [[ -z "$configured" ]] && configured='.agent/ai-hats'
    case "$configured" in
        /*) : ;;
        *) configured="$project_dir/$configured" ;;
    esac
    printf '%s/tracker/backlog/tasks' "$configured"
}

# A path with symlinks resolved, so /tmp and /private/tmp compare equal on macOS.
# A dir that does not exist answers with itself — it cannot be this project's own
# tasks dir either way, and the comparison below is what decides.
realdir() {
    if [[ -d "$1" ]]; then (cd -- "$1" && pwd -P); else printf '%s' "$1"; fi
}

# --- check mode (default) --------------------------------------------------

check_mode() {
    local project_dir="${AI_HATS_PROJECT_DIR:-$PWD}"
    local task_id="${AI_HATS_TASK_ID:-<unnamed>}"

    # --- whose backlog is this? (HATS-1540 R5) -----------------------------
    #
    # DECLARED FAIL-OPEN, and the price is named out loud: a card outside this
    # project's own tracker is not this gate's business, so it passes.
    #
    # Role scope is not backlog scope. A role-scoped binding fires on EVERY
    # backlog the rack CLI touches — including the scratch --tasks-dir that this
    # repo's own rack tests build — and HATS-1538 withdrew the shipped row
    # because the gate refused exactly those. The engine deliberately does not
    # decide this (supervisor ruling 2026-08-08 P1): it fires on the declaration
    # and states the context, and the policy lives here, in the file the role
    # author owns, where it is visible and testable.
    #
    # AI_HATS_TASKS_DIR is absent at `wt:pre-merge`, which is not a backlog
    # operation at all — there is nothing to scope, so the question is skipped.
    local tasks_dir="${AI_HATS_TASKS_DIR:-}"
    if [[ -n "$tasks_dir" ]] && [[ "$(realdir "$tasks_dir")" != "$(realdir "$(own_tasks_dir "$project_dir")")" ]]; then
        printf 'done-gate: %s lives in %s, which is not this project'"'"'s backlog\n' \
               "$task_id" "$tasks_dir"
        printf '(%s). This gate guards what enters THIS repo, so it has no\n' \
               "$(own_tasks_dir "$project_dir")"
        printf 'opinion here. Passing.\n'
        exit 0
    fi

    # A gate that cannot verify must not pass. No dispatcher here means this
    # project has no done-gate stage to run, so no marker could ever be earned
    # honestly — that is a misconfigured binding, and it says so on the first
    # transition rather than on the first card that ships code.
    local dispatcher="$project_dir/scripts/ci-local.sh"
    if [[ ! -f "$dispatcher" ]]; then
        printf 'done-gate: %s does not exist, so this project has no\n' "$dispatcher"
        printf "'done-gate' stage to run and no marker could ever be earned. A gate that\n"
        printf 'cannot verify must not pass. Fix one of the two:\n\n'
        printf '  * add a done-gate stage to scripts/ci-local.sh (ai-hats composes it as\n'
        printf '    lint -> unit -> integration -> merge-smoke), or\n'
        printf '  * unbind the gate: drop the maintainer-quality-gate/hooks/done-gate.sh\n'
        printf "    row from 'composition.checks' in the role that composes this skill.\n"
        exit 2
    fi

    # HATS-1540 R2/H: the tree under judgement arrives in the environment, at
    # BOTH bound points. This used to re-derive the state path from the task id
    # and sed the JSON open — the same lookup ai-hats already owns, open-coded in
    # shell, and the one place a gate can silently end up judging another
    # worktree. The absence of that block is what makes one script serve
    # `edge:review--done` and `wt:pre-merge` without a branch between them.
    local wt branch sha
    wt="${AI_HATS_WORKTREE_PATH:-}"
    # Named only at `wt:pre-merge`, where the wt engine owns the branch; on an
    # FSM edge the branch is rack's own convention. For messages only.
    branch="${AI_HATS_BRANCH_NAME:-task/$(printf '%s' "$task_id" | tr '[:upper:]' '[:lower:]')}"

    if [[ -z "$wt" ]]; then
        # F-11: the subject of this gate is the code entering master through this
        # card. No worktree means no commits of its own, so there is nothing to
        # gate. The runner refuses on its own if it could not TELL (HATS-1540),
        # so an absent variable here means absent, never unknown.
        printf 'done-gate: %s has no worktree — it contributes no commits, so there is\n' \
               "$task_id"
        printf 'nothing to gate. Passing.\n'
        exit 0
    fi

    if [[ ! -d "$wt" ]]; then
        # The record survived its worktree (TMPDIR swept, discarded by hand). Not
        # a hole: rack's own teardown resolves this task to no active worktree and
        # then either finalizes an already-merged branch — nothing new enters
        # master, nothing to gate — or raises WorktreeStateLostError and the
        # transition never reaches a merge (wt_effects.teardown). Gating the
        # branch tip here instead would refuse the first of those, which is a
        # supported recovery flow.
        printf 'done-gate: %s recorded a worktree at %s, which no longer exists — no live\n' \
               "$task_id" "$wt"
        printf 'branch content to gate (rack refuses the merge itself if that branch is\n'
        printf 'unmerged). Passing.\n'
        exit 0
    fi

    # F-13: the TASK BRANCH's tip, never the main checkout's HEAD. At priority 15
    # the merge has not happened yet, so the content under judgement is what the
    # branch holds.
    sha="$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
    if [[ -z "$sha" ]]; then
        printf 'done-gate: could not resolve HEAD of the worktree %s (branch %s). The gate\n' \
               "$wt" "$branch"
        printf 'cannot name the content it is meant to judge, so it refuses.\n'
        exit 2
    fi

    if gate_marker_ok "$GATE_NAME" "$project_dir" "$sha"; then
        printf 'done-gate: green marker present for %s (%s) — passing.\n' "$sha" "$branch"
        exit 0
    fi

    # R6: the refusal is an action, not a diagnosis. One copy-pasteable command,
    # and the second attempt is instant because the marker is already there.
    printf 'done-gate: no green quality-gate marker for %s (branch %s).\n\n' "$sha" "$branch"
    printf 'Run the gate on that exact commit, then retry the transition:\n\n'
    printf '    cd %s && %s\n\n' "$wt" "$RUN_CMD"
    printf 'On green and a clean tree it marks that commit and this transition passes\n'
    printf 'instantly. The marker keys on content, not on time: a new commit needs a\n'
    printf 'new run, and one run covers every card sitting on the same commit.\n'
    exit 2
}

# --- run mode (`--run`) ----------------------------------------------------

run_mode() {
    local repo_root
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -z "$repo_root" ]]; then
        echo "[done-gate] not inside a git repo — gate ABORTED, no marker written" >&2
        exit 1
    fi

    local dispatcher="$repo_root/scripts/ci-local.sh"
    if [[ ! -f "$dispatcher" ]]; then
        echo "[done-gate] no $dispatcher — nothing to run, no marker written" >&2
        exit 1
    fi

    echo "[done-gate] running the done-gate stage in $repo_root (HATS-1137)" >&2
    local rc
    bash "$dispatcher" done-gate || {
        rc=$?
        echo "[done-gate] the done-gate stage FAILED (rc=$rc) — NO marker written." >&2
        exit 1
    }

    local head_sha
    head_sha="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
    if [[ -z "$head_sha" ]]; then
        echo "[done-gate] stage passed but HEAD does not resolve — NO marker written" >&2
        exit 1
    fi

    # F-14: a marker describes COMMITTED content. The gate ran against the working
    # tree, so on a dirty tree what passed is not what the branch tip holds.
    if [[ -n "$(git -C "$repo_root" status --porcelain 2>/dev/null)" ]]; then
        cat >&2 <<EOF
[done-gate] stage passed BUT the working tree is dirty — NO marker written.
The marker must describe the committed content the transition will judge.
Commit (or stash), then re-run so the marker matches HEAD ($head_sha).
EOF
        exit 0
    fi

    local marker
    marker="$(gate_marker_write "$GATE_NAME" "$repo_root" "$head_sha" 'stage=done-gate')" || {
        echo "[done-gate] stage passed but the marker could not be written" >&2
        exit 1
    }
    echo "[done-gate] green — wrote $marker" >&2
    echo "[done-gate] 'rack transition <ID> done' on this commit now passes instantly." >&2
    exit 0
}

# --- dispatch --------------------------------------------------------------

# The check runner spawns this with no argv at all, so --check is the default.
case "${1:---check}" in
    --check) check_mode ;;
    --run) run_mode ;;
    *)
        echo "usage: done-gate.sh [--check|--run]" >&2
        # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
        # neither a refusal nor a verdict of any kind.
        exit 64
        ;;
esac
