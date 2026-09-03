#!/usr/bin/env bash
# The channel side of a gate — what the library still owns once the stages,
# the marker and the run moved into the project (ADR-0023 D7).
#
# A gate is a NAME for a set of stages; the project declares the set in
# `scripts/gates.sh` and earns markers for it with `scripts/ci-gate.sh`. What is
# left for a hook to do is: say WHICH tree this transition puts into master,
# ask the project whether that tree has earned the gate, and speak the answer
# in its own channel's exit codes. Nothing here names a stage or a gate.
#
# Sourced, never executed; callers keep their own `set` options.
#
#   gate_tree                <in_dir> <rev>    -> prints the tree of <rev>
#   gate_check_task_worktree <gate>            -> EXITS in the checks channel
#   gate_refusal             <gate> <tree> <label> <cmd> <missing>
#   gate_exit                <channel> pass|refuse

# The tree of a revision. The unit of judgement is CONTENT, so a --no-ff merge
# over an unchanged tree is the same subject as the branch tip.
gate_tree() {
    (cd "$1" 2>/dev/null && git rev-parse "$2^{tree}" 2>/dev/null) || return 1
}

# The refusal is an action, not a diagnosis: what is missing, and the one
# command that earns it. Every stage already green stays green — the retry
# after that command pays only for what this list names.
gate_refusal() {
    local gate="$1" tree="$2" label="$3" cmd="$4" missing="$5"
    printf '%s: tree %s (%s) has not earned every stage this gate requires.\n\n' \
           "$gate" "$tree" "$label"
    printf 'Missing:\n'
    local stage
    for stage in $missing; do
        printf '    %s\n' "$stage"
    done
    printf '\nRun the gate on that exact content, then retry:\n\n    %s\n\n' "$cmd"
    printf 'It runs only what is missing and stamps each green stage for the tree, so\n'
    printf 'the retry is instant, and any wider gate whose set includes these stages\n'
    printf 'stamps them too (`scripts/gates.sh list` names the gates).\n'
}

# --- whose backlog is this? ------------------------------------------------
#
# This project's OWN tasks dir, from `ai-hats.yaml` and the documented default —
# deliberately NOT from AI_HATS_DIR. That variable is the leaky one: a value
# inherited from another checkout points at a tracker of its own, and the whole
# question is which tracker this transition belongs to.
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

# A path with symlinks resolved, so /tmp and /private/tmp compare equal on macOS.
# A dir that does not exist answers with itself — it cannot be this project's own
# tasks dir either way, and the comparison is what decides.
_gate_realdir() {
    if [[ -d "$1" ]]; then (cd -- "$1" && pwd -P); else printf '%s' "$1"; fi
}

# --- check mode, whole ------------------------------------------------------

# Require every stage of <gate> to be marked for the tree this card puts into
# master. EXITS. Instant by construction — a few `git rev-parse` and a marker
# lookup — because it runs INSIDE the per-task rack lock. The suite can never
# run here: that is `scripts/gates.sh run <gate>`'s job, out of band.
#
# Role-owned rather than engine-owned: the backlog-scope policy below is this
# repository's, and it stays where its author can see and test it.
gate_check_task_worktree() {
    local gate="$1"
    local project_dir="${AI_HATS_PROJECT_DIR:-$PWD}"
    local task_id="${AI_HATS_TASK_ID:-<unnamed>}"

    # DECLARED FAIL-OPEN, and the price is named out loud: a card outside this
    # project's own tracker is not this gate's business, so it passes. A
    # role-scoped binding fires on EVERY backlog the rack CLI touches — the
    # scratch --tasks-dir this repo's own rack tests build included. Absent at
    # `wt:pre-merge`, which is not a backlog operation at all.
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
    # Named only at `wt:pre-merge`, where the wt engine owns the branch; on an
    # FSM edge the branch is rack's own convention. For messages only.
    branch="${AI_HATS_BRANCH_NAME:-task/$(printf '%s' "$task_id" | tr '[:upper:]' '[:lower:]')}"

    if [[ -z "$wt" && -z "$merged" ]]; then
        # The subject is the code entering master through this card. No worktree
        # AND nothing merged means no commits of its own. The runner refuses on
        # its own if it could not TELL, so absent here means absent, never unknown.
        printf '%s: %s has no worktree — it contributes no commits, so there is\n' \
               "$gate" "$task_id"
        printf 'nothing to gate. Passing.\n'
        gate_exit checks pass
    fi

    if [[ -n "$wt" && ! -d "$wt" && -z "$merged" ]]; then
        # The record survived its worktree and NOTHING was merged. Not a hole:
        # rack's own teardown either finalizes an already-merged branch — nothing
        # new enters master — or refuses the transition. `-z "$merged"` is
        # load-bearing: with a merge record in hand this pass would be the very
        # hole the second name exists to close.
        printf '%s: %s recorded a worktree at %s, which no longer exists — no live\n' \
               "$gate" "$task_id" "$wt"
        printf 'branch content to gate (rack refuses the merge itself if that branch is\n'
        printf 'unmerged). Passing.\n'
        gate_exit checks pass
    fi

    # Two ways to name the content this card puts into master, and the tree is
    # the subject either way. A LIVE directory picks the road: a record whose
    # worktree is gone but whose branch reached the base belongs to the second.
    local run_in rev subject run_cmd
    if [[ -d "$wt" ]]; then
        # The TASK BRANCH's tree, never the main checkout's: the merge has not
        # happened yet, so what the branch holds IS what lands.
        run_in="$wt"
        rev='HEAD'
        subject="branch $branch"
        run_cmd="cd $wt && make $gate"
    else
        # The worktree is gone because the branch already reached the base
        # branch, so what this card put into master is that merge commit. The
        # main checkout's own `scripts/gates.sh` answers for it; the RUN judges
        # the commit in a checkout of its own, which `REV=` names.
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
    # says what the gate requires and reads the markers. A tree that names no
    # such file cannot earn a marker honestly, and a gate that cannot verify
    # must not pass.
    local gates="$run_in/scripts/gates.sh"
    if [[ ! -f "$gates" ]]; then
        printf '%s: %s has no scripts/gates.sh, so no marker could ever be earned\n' \
               "$gate" "$run_in"
        printf 'honestly. Fix one of the two:\n\n'
        printf '  * give the project a scripts/gates.sh whose table names %s, or\n' "$gate"
        printf "  * unbind the gate: drop the row carrying 'gate: %s' from\n" "$gate"
        printf "    'composition.apps' in the role that composes this skill.\n"
        gate_exit checks refuse
    fi

    local missing rc
    missing="$(cd "$run_in" && bash "$gates" check "$gate" --rev "$rev")"
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
        64)
            printf '%s: %s knows no gate named %s (see its `list`). A gate that\n' \
                   "$gate" "$gates" "$gate"
            printf 'cannot verify must not pass — add the row to its table, or unbind it.\n'
            gate_exit checks refuse
            ;;
        *)
            # Not a verdict: the primitive could not tell. Exit 1 is "the gate
            # broke" in this channel (ADR-0020 D2) — never a pass, and never
            # spelled 2, which would claim a refusal nobody made.
            printf '%s: `%s check %s` failed with rc=%s — the gate could not tell.\n' \
                   "$gate" "$gates" "$gate" "$rc"
            exit 1
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
