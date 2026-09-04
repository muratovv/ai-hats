#!/usr/bin/env bash
# What every thin gate shares. A gate is a few lines: the stages it requires and
# a call to `gate_main`; this file is everything that call does.
#
# Sourced, never executed; callers keep their own `set` options.
#
#   gate_main <gate> "<stages>" [--check | --run [--rev <sha>] | --stages]
#       --check (DEFAULT, what a bare spawn from the checks channel gets):
#           say which tree this transition puts into master, ask that tree's
#           `scripts/gates.sh check` about the stages, exit 0 pass / 2 refuse.
#       --run: `scripts/gates.sh run <stages>` — earn them, in place when this
#           checkout is clean and at the subject, in a scratch checkout otherwise.
#       --stages: print the list, for a reader or a renderer.
#
#   gate_tree <in_dir> <rev>            -> the tree of <rev>
#   gate_refusal <gate> <tree> <label> <cmd> <missing>
#   gate_exit <channel> pass|refuse     -> exits with that channel's code
#
# Nothing here names a stage: what a gate requires is the gate's own line, and
# how a stage runs is `scripts/gates.sh`'s.

gate_tree() {
    (cd "$1" 2>/dev/null && git rev-parse "$2^{tree}" 2>/dev/null) || return 1
}

# The refusal is an action, not a diagnosis: what is missing, and the one
# command that earns it — nothing after the command, which is what a reader
# who arrives at the tail sees first.
gate_refusal() {
    local gate="$1" tree="$2" label="$3" cmd="$4" missing="$5"
    printf '%s: tree %s (%s) has not earned every stage this gate requires.\n\n' \
           "$gate" "$tree" "$label"
    printf 'Missing:\n'
    local stage
    for stage in $missing; do
        printf '    %s\n' "$stage"
    done
    printf '\nRun the gate on that exact content, then retry:\n\n    %s\n' "$cmd"
}

# This project's OWN tasks dir, from `ai-hats.yaml` and the documented default —
# deliberately NOT from AI_HATS_DIR, which leaks between checkouts.
_gate_own_tasks_dir() {
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

# Symlinks resolved, so /tmp and /private/tmp compare equal on macOS.
_gate_realdir() {
    if [[ -d "$1" ]]; then (cd -- "$1" && pwd -P); else printf '%s' "$1"; fi
}

# Require every stage of <gate> to be marked for the tree this card puts into
# master. EXITS. Instant by construction — a few `git rev-parse` and a marker
# lookup — because it runs INSIDE the per-task rack lock. The suite never runs
# here: that is `--run`, out of band.
gate_check_task_worktree() {
    local gate="$1" stages="$2"
    local project_dir="${AI_HATS_PROJECT_DIR:-$PWD}"
    local task_id="${AI_HATS_TASK_ID:-<unnamed>}"

    # DECLARED FAIL-OPEN, and the price is named out loud: a card outside this
    # project's own tracker is not this gate's business. A role-scoped binding
    # fires on EVERY backlog the rack CLI touches — the scratch --tasks-dir this
    # repo's own rack tests build included. Absent at `wt:pre-merge`.
    local tasks_dir="${AI_HATS_TASKS_DIR:-}" own
    own="$(_gate_own_tasks_dir "$project_dir")"
    if [[ -n "$tasks_dir" ]] && [[ "$(_gate_realdir "$tasks_dir")" != "$(_gate_realdir "$own")" ]]; then
        printf '%s: %s lives in %s, which is not this project'"'"'s backlog\n' \
               "$gate" "$task_id" "$tasks_dir"
        printf '(%s). This gate guards what enters THIS repo, so it has no\n' "$own"
        printf 'opinion here. Passing.\n'
        gate_exit checks pass
    fi

    # The tree under judgement arrives in the environment, at BOTH bound points;
    # re-deriving it from the task id is the one place a gate can end up judging
    # another worktree.
    local wt branch merged
    wt="${AI_HATS_WORKTREE_PATH:-}"
    # Set only once the worktree is gone AND its branch reached the base branch,
    # which is what separates "brought no code" from "already merged".
    merged="${AI_HATS_MERGED_SHA:-}"
    branch="${AI_HATS_BRANCH_NAME:-task/$(printf '%s' "$task_id" | tr '[:upper:]' '[:lower:]')}"

    if [[ -z "$wt" && -z "$merged" ]]; then
        # No worktree AND nothing merged means no commits of its own. The runner
        # refuses on its own if it could not TELL, so absent means absent.
        printf '%s: %s has no worktree — it contributes no commits, so there is\n' \
               "$gate" "$task_id"
        printf 'nothing to gate. Passing.\n'
        gate_exit checks pass
    fi

    if [[ -n "$wt" && ! -d "$wt" && -z "$merged" ]]; then
        # The record survived its worktree and NOTHING was merged: rack's own
        # teardown either finalizes an already-merged branch or refuses the
        # transition. `-z "$merged"` is load-bearing.
        printf '%s: %s recorded a worktree at %s, which no longer exists — no live\n' \
               "$gate" "$task_id" "$wt"
        printf 'branch content to gate (rack refuses the merge itself if that branch is\n'
        printf 'unmerged). Passing.\n'
        gate_exit checks pass
    fi

    # Two ways to name the content this card puts into master; a LIVE directory
    # picks the road.
    local run_in rev subject run_cmd
    if [[ -d "$wt" ]]; then
        run_in="$wt"
        rev='HEAD'
        subject="branch $branch"
        run_cmd="cd $wt && make $gate"
    else
        # The worktree is gone because the branch already reached the base
        # branch, so what this card put into master is that merge commit; the
        # RUN judges it in a checkout of its own, which `REV=` names.
        run_in="$project_dir"
        rev="$merged"
        subject="merge commit $merged"
        run_cmd="cd $project_dir && make $gate REV=$merged"
    fi

    local tree
    tree="$(gate_tree "$run_in" "$rev")"
    if [[ -z "$tree" ]]; then
        printf '%s: could not resolve the tree of %s (%s). The\n' "$gate" "$subject" "$branch"
        printf 'gate cannot name the content it is meant to judge, so it refuses.\n'
        gate_exit checks refuse
    fi

    # The project under judgement answers for itself: its own `scripts/gates.sh`
    # runs the stages and reads the markers. A tree that has none cannot earn a
    # marker honestly, and a gate that cannot verify must not pass.
    local gates="$run_in/scripts/gates.sh"
    if [[ ! -f "$gates" ]]; then
        printf '%s: %s has no scripts/gates.sh, so no marker could ever be earned\n' \
               "$gate" "$run_in"
        printf 'honestly. Give the project one, or unbind the gate: drop the\n'
        printf "maintainer-quality-gate/hooks/%s.sh row from 'composition.apps' in the\n" "$gate"
        printf 'role that composes this skill.\n'
        gate_exit checks refuse
    fi

    local missing rc
    # shellcheck disable=SC2086
    missing="$(cd "$run_in" && bash "$gates" check --rev "$rev" $stages)"
    rc=$?
    case "$rc" in
        0)
            printf '%s: every required stage is green for tree %s (%s) — passing.\n' \
                   "$gate" "$tree" "$subject"
            gate_exit checks pass
            ;;
        1)
            gate_refusal "$gate" "$tree" "$subject" "$run_cmd" "$missing"
            gate_exit checks refuse
            ;;
        *)
            # Not a verdict: the primitive could not tell. Exit 1 is "the gate
            # broke" in this channel (ADR-0020 D2) — never a pass, and never
            # spelled 2, which would claim a refusal nobody made.
            printf '%s: `%s check` failed with rc=%s — the gate could not tell.\n' \
                   "$gate" "$gates" "$rc"
            exit 1
            ;;
    esac
}

# Earn the stages: `scripts/gates.sh run` of the checkout the caller stands in.
# EXITS with the run's rc, after naming the command that resumes a red one.
gate_run() {
    local gate="$1" stages="$2"
    shift 2
    local repo_root
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        printf '[%s] not inside a git repository — nothing to run\n' "$gate" >&2
        exit 70
    }
    local gates="$repo_root/scripts/gates.sh"
    if [[ ! -f "$gates" ]]; then
        printf '[%s] %s has no scripts/gates.sh — nothing can earn this gate here\n' \
               "$gate" "$repo_root" >&2
        exit 70
    fi
    printf '[%s] earning: %s\n' "$gate" "$stages" >&2
    local rc=0
    # shellcheck disable=SC2086
    bash "$gates" run "$@" $stages || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        # The primitive ends with its RESULT line; the retry is the one line it
        # cannot write, because no gate name reaches it. "fix … then", not
        # "retry": read alone, a bare retry under FAILED master-ci is a loop.
        local rev='' prev='' arg
        for arg in "$@"; do
            [[ "$prev" == "--rev" ]] && rev="$arg"
            prev="$arg"
        done
        printf '[%s] fix the FAILED stage above, then: make %s%s\n' "$gate" "$gate" "${rev:+ REV=$rev}" >&2
    fi
    exit "$rc"
}

# The thin gate's whole body. The check runner spawns a gate with no argv at
# all, so --check is the default.
gate_main() {
    local gate="$1" stages="$2"
    shift 2
    case "${1:---check}" in
        --check) gate_check_task_worktree "$gate" "$stages" ;;
        --run)
            shift
            gate_run "$gate" "$stages" "$@"
            ;;
        --stages) printf '%s\n' "$stages" ;;
        *)
            echo "usage: $gate.sh [--check | --run [--rev <sha>] | --stages]" >&2
            # 64 = EX_USAGE. Never 1 and never 2: a typo at the command line is
            # neither a refusal nor a verdict of any kind.
            exit 64
            ;;
    esac
}

# One verdict, two spellings. git reads 0/1; the checks channel reads 0 pass,
# 2 refuse, 126/127 corrupt, everything else "the gate broke" (ADR-0020 D2) —
# which is why a refusal must never be spelled 1 there.
gate_exit() {
    local channel="$1" outcome="$2"
    case "$outcome" in
        pass) exit 0 ;;
        refuse) [[ "$channel" == "checks" ]] && exit 2 || exit 1 ;;
        *)
            printf 'gate_exit: unknown outcome %s\n' "$outcome" >&2
            exit 64
            ;;
    esac
}
