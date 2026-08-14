#!/usr/bin/env bash
# HATS-1604 — the gate primitive (ADR-0023 D6). A gate is a primitive, not a
# script: the discipline below was written by hand twice, in
# `git_hooks/pre-push-e2e-master.sh` and `hooks/done-gate.sh`, and the two copies
# had already begun to drift.
#
# The primitive owns: resolving the stage dispatcher and its composition, running
# that composition stopping at the first red, the rule "green AND a clean tree ->
# marker; dirty -> the run happened, no marker", the marker lookup, the refusal
# text with its copy-pasteable command, and mapping an outcome onto the exit
# codes of the CALLER'S channel.
#
# A concrete gate keeps exactly two decisions: WHICH TREE it judges and WHAT runs
# on it. Everything else is here.
#
# Sourced, never executed; callers keep their own `set` options. Requires
# gate-marker.sh to be sourced first.
#
#   gate_tree     <in_dir> <rev>                -> prints the tree of <rev>
#   gate_stages   <dispatcher> <gate>           -> prints the gate's composition
#   gate_run      <dispatcher> <gate> [log]     -> runs it; rc is the first red
#   gate_stamp    <gate> <repo_root> <tree> <stages> [line...] -> marks a clean tree
#   gate_refusal  <gate> <tree> <label> <cmd> <stages>  -> prints the refusal
#   gate_exit     <channel> pass|refuse         -> exits with that channel's code
#
# The two whole modes, for a checks-channel gate on a task's worktree — each
# EXITS, it does not return (HATS-1614, when a second gate made them a copy):
#
#   gate_check_task_worktree <gate> <run_cmd>
#   gate_run_and_stamp_here  <gate> <next_step>

# The tree of a revision. The unit of judgement is CONTENT, so a --no-ff merge
# over an unchanged tree is the same subject as the branch tip (HATS-1601).
gate_tree() {
    (cd "$1" 2>/dev/null && git rev-parse "$2^{tree}" 2>/dev/null) || return 1
}

# The composition, from the project's dispatcher — never a literal here. The
# library owns the discipline, the project owns what its gate runs (ADR-0023 D7).
#
# `--stages` goes FIRST, and that position is load-bearing: a dispatcher that
# predates this contract sees an unknown stage name and refuses in milliseconds.
# Asked the other way round it would read `--stages` as a trailing argument to a
# gate it does know — and RUN it, inside a 20s in-lock budget (measured: a
# 2-minute hang against the pre-1604 dispatcher).
gate_stages() {
    local dispatcher="$1" gate="$2"
    [[ -f "$dispatcher" ]] || return 1
    bash "$dispatcher" --stages "$gate" 2>/dev/null || return 1
}

# Run the composition, stopping at the first red; rc is that stage's rc. With a
# log path the output is both shown and captured, so a caller can quote the tail
# in its own diagnosis.
gate_run() {
    local dispatcher="$1" gate="$2" log="${3:-}"
    local stages stage rc
    stages="$(gate_stages "$dispatcher" "$gate")" || return 1
    for stage in $stages; do
        if [[ -n "$log" ]]; then
            bash "$dispatcher" "$stage" 2>&1 | tee -a "$log"
            rc="${PIPESTATUS[0]}"
        else
            bash "$dispatcher" "$stage"
            rc=$?
        fi
        if [[ "$rc" -ne 0 ]]; then
            printf "[%s] stage '%s' FAILED (rc=%s) — stopping here, NO marker written\n" \
                   "$gate" "$stage" "$rc" >&2
            return "$rc"
        fi
    done
    return 0
}

# Mark a green run — but only when the tree is clean. A marker describes
# COMMITTED content, and on a dirty tree what passed is not what the branch
# holds. rc 0 either way: the run itself succeeded, the marker just isn't honest.
gate_stamp() {
    local gate="$1" repo_root="$2" tree="$3" stages="$4"
    shift 4
    if [[ -n "$(git -C "$repo_root" status --porcelain 2>/dev/null)" ]]; then
        printf '[%s] green BUT the working tree is dirty — NO marker written.\n' "$gate" >&2
        printf 'Commit (or stash), then re-run so the marker matches the tree that ships.\n' >&2
        return 0
    fi
    local marker
    marker="$(gate_marker_write "$gate" "$repo_root" "$tree" "$stages" ${@+"$@"})" || {
        printf '[%s] green but the marker could not be written\n' "$gate" >&2
        return 1
    }
    printf '[%s] green — wrote %s\n' "$gate" "$marker" >&2
}

# The refusal is an action, not a diagnosis: one copy-pasteable command, and the
# retry is instant because the marker is already there. The composition is
# RENDERED from the dispatcher, never restated here (ADR-0023 D7).
gate_refusal() {
    local gate="$1" tree="$2" label="$3" cmd="$4" stages="$5"
    printf '%s: no green marker for tree %s (%s).\n\n' "$gate" "$tree" "$label"
    printf 'Run the gate on that exact content, then retry:\n\n    %s\n\n' "$cmd"
    printf 'It runs %s.\n' "${stages:-the stages this project composes}"
    printf 'The marker keys on the tree, so a merge that changes nothing reuses this\n'
    printf 'run, and one run covers every card sitting on the same content.\n'
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

# Require a green marker for the tree of the task's worktree branch tip. EXITS.
#
# Instant by construction: a few `git rev-parse` and one file read, because it
# runs INSIDE the per-task rack lock (priority 15, budget 20s against a 30s
# lock). The suite can never run here — that is what `--run` is for.
#
# HATS-1614 moved this out of `hooks/done-gate.sh`: a second gate on the same
# channel would have copied all of it to change two strings, which is the drift
# HATS-1604 removed one layer down. Role-owned either way — this file ships in
# the same skill as the gates, not in the engine, so the backlog-scope policy
# below stays where its author can see and test it (supervisor ruling
# 2026-08-08 P1).
gate_check_task_worktree() {
    local gate="$1" run_cmd="$2"
    local project_dir="${AI_HATS_PROJECT_DIR:-$PWD}"
    local task_id="${AI_HATS_TASK_ID:-<unnamed>}"

    # DECLARED FAIL-OPEN, and the price is named out loud: a card outside this
    # project's own tracker is not this gate's business, so it passes.
    #
    # Role scope is not backlog scope. A role-scoped binding fires on EVERY
    # backlog the rack CLI touches — including the scratch --tasks-dir this
    # repo's own rack tests build — and HATS-1538 withdrew the shipped row
    # because the gate refused exactly those. The engine deliberately does not
    # decide this: it fires on the declaration and states the context.
    #
    # AI_HATS_TASKS_DIR is absent at `wt:pre-merge`, which is not a backlog
    # operation at all — nothing to scope, so the question is skipped.
    local tasks_dir="${AI_HATS_TASKS_DIR:-}" own
    own="$(_gate_own_tasks_dir "$project_dir")"
    if [[ -n "$tasks_dir" ]] && [[ "$(_gate_realdir "$tasks_dir")" != "$(_gate_realdir "$own")" ]]; then
        printf '%s: %s lives in %s, which is not this project'"'"'s backlog\n' \
               "$gate" "$task_id" "$tasks_dir"
        printf '(%s). This gate guards what enters THIS repo, so it has no\n' "$own"
        printf 'opinion here. Passing.\n'
        gate_exit checks pass
    fi

    # HATS-1540 R2/H: the tree under judgement arrives in the environment, at
    # BOTH bound points. This used to re-derive the state path from the task id
    # and sed the JSON open — the one place a gate can silently end up judging
    # another worktree.
    local wt branch tree dispatcher stages
    wt="${AI_HATS_WORKTREE_PATH:-}"
    # Named only at `wt:pre-merge`, where the wt engine owns the branch; on an
    # FSM edge the branch is rack's own convention. For messages only.
    branch="${AI_HATS_BRANCH_NAME:-task/$(printf '%s' "$task_id" | tr '[:upper:]' '[:lower:]')}"

    if [[ -z "$wt" ]]; then
        # F-11: the subject is the code entering master through this card. No
        # worktree means no commits of its own. The runner refuses on its own if
        # it could not TELL (HATS-1540), so absent here means absent, never
        # unknown.
        printf '%s: %s has no worktree — it contributes no commits, so there is\n' \
               "$gate" "$task_id"
        printf 'nothing to gate. Passing.\n'
        gate_exit checks pass
    fi

    if [[ ! -d "$wt" ]]; then
        # The record survived its worktree (TMPDIR swept, discarded by hand).
        # Not a hole: rack's own teardown resolves this task to no active
        # worktree and then either finalizes an already-merged branch — nothing
        # new enters master — or raises WorktreeStateLostError and the transition
        # never reaches a merge. Gating the branch tip here instead would refuse
        # the first of those, a supported recovery flow.
        printf '%s: %s recorded a worktree at %s, which no longer exists — no live\n' \
               "$gate" "$task_id" "$wt"
        printf 'branch content to gate (rack refuses the merge itself if that branch is\n'
        printf 'unmerged). Passing.\n'
        gate_exit checks pass
    fi

    # F-13: the TASK BRANCH's tree, never the main checkout's. At priority 15 the
    # merge has not happened yet, so the content under judgement is what the
    # branch holds — and content, not the commit naming it, is the subject.
    tree="$(gate_tree "$wt" HEAD)"
    if [[ -z "$tree" ]]; then
        printf '%s: could not resolve the tree of the worktree %s (branch %s). The\n' \
               "$gate" "$wt" "$branch"
        printf 'gate cannot name the content it is meant to judge, so it refuses.\n'
        gate_exit checks refuse
    fi

    # The composition comes from the dispatcher IN THE TREE UNDER JUDGEMENT, not
    # from the main checkout's. The marker certifies stages that were run there,
    # so asking anywhere else judges one tree by another tree's rules. A card
    # that changes the gate carries the change and its own verdict together.
    dispatcher="$wt/scripts/ci-local.sh"
    stages="$(gate_stages "$dispatcher" "$gate")"
    if [[ -z "$stages" ]]; then
        printf '%s: %s names no %s composition, so no marker could ever be\n' \
               "$gate" "$dispatcher" "$gate"
        printf 'earned honestly. A gate that cannot verify must not pass. Fix one of the two:\n\n'
        printf '  * have the dispatcher answer `--stages %s` with the stages this\n' "$gate"
        printf '    project wants the gate to run, or\n'
        printf '  * unbind the gate: drop the maintainer-quality-gate/hooks/%s.sh\n' "$gate"
        printf "    row from 'composition.apps' in the role that composes this skill.\n"
        gate_exit checks refuse
    fi

    if gate_marker_ok "$project_dir" "$tree" $stages; then
        printf '%s: green marker present for tree %s (%s) — passing.\n' "$gate" "$tree" "$branch"
        gate_exit checks pass
    fi

    gate_refusal "$gate" "$tree" "branch $branch" "cd $wt && $run_cmd" "$stages"
    gate_exit checks refuse
}

# --- run mode, whole --------------------------------------------------------

# Run the gate here and mark this tree on green. EXITS. `next_step` is the one
# line telling the agent what the run just bought.
gate_run_and_stamp_here() {
    local gate="$1" next_step="$2"
    local repo_root
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -z "$repo_root" ]]; then
        printf '[%s] not inside a git repo — gate ABORTED, no marker written\n' "$gate" >&2
        exit 1
    fi

    local dispatcher="$repo_root/scripts/ci-local.sh" stages
    stages="$(gate_stages "$dispatcher" "$gate")"
    if [[ -z "$stages" ]]; then
        printf '[%s] %s names no %s composition — nothing to run\n' "$gate" "$dispatcher" "$gate" >&2
        exit 1
    fi

    printf '[%s] running %s in %s: %s\n' "$gate" "$gate" "$repo_root" "$stages" >&2
    gate_run "$dispatcher" "$gate" || exit 1

    local tree
    tree="$(gate_tree "$repo_root" HEAD)"
    if [[ -z "$tree" ]]; then
        printf '[%s] green but HEAD'"'"'s tree does not resolve — NO marker written\n' "$gate" >&2
        exit 1
    fi

    gate_stamp "$gate" "$repo_root" "$tree" "$stages" || exit 1
    printf '[%s] %s\n' "$gate" "$next_step" >&2
    exit 0
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
