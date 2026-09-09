#!/usr/bin/env bash
# What every thin gate shares. A gate is a few lines: the stages it requires and
# a call to `gate_main`; this file is everything that call does.
#
# Sourced, never executed; callers keep their own `set` options.
#
# HATS-1634 — the refusal exits through `gate_exit`, so that is where it is
# counted. Every literal `exit 1` in a gate above means the gate BROKE, not that
# it refused; recording those would file a breakage as a verdict.
# shellcheck source=../../../../hooks/bypass_journal.sh
if ! . "${AI_HATS_BYPASS_JOURNAL:-$(dirname "$0")/../../../../hooks/bypass_journal.sh}" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi
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
# A gate may declare RUN_CMD (what earns it; default `make <gate>`) and
# RUN_REV_FMT (how that command names a commit; default `REV=%s`).
#
# Nothing here names a stage: what a gate requires is the gate's own line, and
# how a stage runs is `scripts/gates.sh`'s.

# This file is `<skill>/lib/gate.sh`, so the gates are its siblings' contents.
# From BASH_SOURCE rather than $0: a gate is spawned by make, by git and by the
# check runner, and only this is the same under all three.
_GATE_SKILL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

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

# The zone stages the judged tree says this change demands, into
# `_GATE_ZONE_STAGES`. Non-zero return means the gate must not proceed.
#
# DECLARED FAIL-OPEN, price named: a tree whose `gates.sh` predates the verb has
# no zone stages either, so there is nothing there to demand and today's level is
# what it gets. That is a different thing from a tree that HAS the verb and could
# not answer — which is a gate that cannot tell, and refuses.
_GATE_ZONE_STAGES=''
_gate_zone_stages() {
    local gate="$1" run_in="$2" gates="$3" rev="$4" out rc=0
    _GATE_ZONE_STAGES=''
    if ! grep -q '^cmd_touched()' "$gates" 2>/dev/null; then
        printf '%s: %s predates zone selection — no zone stage can be demanded\n' \
               "$gate" "$gates"
        printf 'of this tree. Requiring only what the gate declares.\n'
        return 0
    fi
    out="$(cd "$run_in" && bash "$gates" touched --rev "$rev" 2>&1)" || rc=$?
    if [[ $rc -ne 0 ]]; then
        printf '%s: `%s touched` failed with rc=%s — the gate could not tell\n' \
               "$gate" "$gates" "$rc"
        printf 'which zones this change demands, and an empty answer would read as\n'
        printf '"none". It said:\n%s\n' "$out"
        return 1
    fi
    _GATE_ZONE_STAGES="$(printf '%s' "$out" | tr '\n' ' ')"
    return 0
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
        printf "quality-gate/hooks/%s.sh row from 'composition.apps' in the\n" "$gate"
        printf 'role that composes this skill.\n'
        gate_exit checks refuse
    fi

    # What this CHANGE additionally demands, on top of what the gate declares.
    # It cannot live in `$STAGES`: the answer depends on the diff, and the same
    # tree asks for different sets as the base branch moves. What it names are
    # ordinary named stages all the same, so markers stay per stage per tree and
    # nothing about them has to know a diff ever happened.
    _gate_zone_stages "$gate" "$run_in" "$gates" "$rev" || exit 1
    stages="$stages $_GATE_ZONE_STAGES"

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

# Every gate this skill carries, card gates first. A gate that cannot be listed
# here is a gate nothing can ask about.
_gate_scripts() {
    local dir script
    for dir in "$_GATE_SKILL_DIR/hooks" "$_GATE_SKILL_DIR/git_hooks"; do
        [[ -d "$dir" ]] || continue
        for script in "$dir"/*.sh; do
            if [[ -f "$script" ]]; then
                printf '%s\n' "$script"
            fi
        done
    done
}

# A gate's name: its own `GATE=` when it declares one (the git hooks, whose file
# name is about git), otherwise the script's basename (the card gates, whose
# file name IS the name the role binds and `make` spells).
_gate_name_of() {
    local script="$1" declared base
    declared="$(sed -n "s/^GATE='\([^']*\)'.*/\1/p" "$script" 2>/dev/null | head -1)"
    if [[ -n "$declared" ]]; then
        printf '%s' "$declared"
        return 0
    fi
    base="$(basename -- "$script")"
    printf '%s' "${base%.sh}"
}

# What the NEXT transition will additionally demand, said once after a green run
# so the road costs one refusal fewer. Read from the MARKERS, never from a set
# difference: a stage two gates share can still be red, and one this run has
# just earned must not be named. The gate that ran needs no excluding — its own
# stages are all marked now, so it drops out by the same rule as the rest.
gate_next_note() {
    local repo_root="$1" gates="$2" rev="$3"
    local -a sets=() names=() counts=() args=(check)
    if [[ -n "$rev" ]]; then
        args+=(--rev "$rev")
    fi
    local script name stages missing rc i idx found unanswered='' used='' line='' verb
    while IFS= read -r script; do
        name="$(_gate_name_of "$script")"
        if ! stages="$(bash "$script" --stages 2>/dev/null)" || [[ -z "$stages" ]]; then
            unanswered="$unanswered $name"
            continue
        fi
        # A gate demands its declared set PLUS this change's zones, so a note
        # built from `--stages` alone would promise a road one refusal shorter
        # than it is — the exact thing this note exists to prevent.
        stages="$stages $_GATE_ZONE_STAGES"
        # shellcheck disable=SC2086
        missing="$(cd "$repo_root" && bash "$gates" "${args[@]}" $stages)"
        rc=$?
        if [[ $rc -gt 1 ]]; then
            unanswered="$unanswered $name"
            continue
        fi
        missing="$(printf '%s' "$missing" | tr '\n' ' ' | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//')"
        [[ -n "$missing" ]] || continue
        found=''
        for ((i = 0; i < ${#sets[@]}; i++)); do
            if [[ "${sets[$i]}" == "$missing" ]]; then
                names[$i]="${names[$i]}, $name"
                found=1
                break
            fi
        done
        if [[ -z "$found" ]]; then
            sets+=("$missing")
            names+=("$name")
            counts+=("$(printf '%s' "$missing" | wc -w | tr -d ' ')")
        fi
    done < <(_gate_scripts)

    # Cheapest step first: the fewest stages still to earn.
    while :; do
        idx=-1
        for ((i = 0; i < ${#sets[@]}; i++)); do
            case " $used " in *" $i "*) continue ;; esac
            if [[ $idx -lt 0 || ${counts[$i]} -lt ${counts[$idx]} ]]; then
                idx=$i
            fi
        done
        [[ $idx -ge 0 ]] || break
        used="$used $idx"
        case "${names[$idx]}" in
            *,*) verb='also need' ;;
            *) verb='also needs' ;;
        esac
        line="${line:+$line; }${names[$idx]} $verb $(printf '%s' "${sets[$idx]}" | sed 's/ /, /g')"
    done

    if [[ -n "$line" ]]; then
        printf '[gates] next: %s\n' "$line" >&2
    else
        printf '[gates] next: nothing — every gate is green for this tree\n' >&2
    fi
    if [[ -n "$unanswered" ]]; then
        printf '[gates] next: could not ask%s what it requires\n' "$unanswered" >&2
    fi
}

# Earn the stages: `scripts/gates.sh run` of the checkout the caller stands in,
# told what command resumes a red run. EXITS with the run's rc.
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
    # The primitive prints the resume command second in its block, right after
    # the verdict; it cannot spell it, because no gate name reaches it. A gate
    # earned by something other than `make <gate> REV=<sha>` says so in RUN_CMD
    # and RUN_REV_FMT — the push gate is earned by a script, and the resume line
    # used to name `make push-gate`, a target nothing defines.
    local rev='' prev='' arg
    for arg in "$@"; do
        [[ "$prev" == "--rev" ]] && rev="$arg"
        prev="$arg"
    done
    local rev_arg=''
    if [[ -n "$rev" ]]; then
        # The format is a gate's own declaration, never input.
        # shellcheck disable=SC2059
        printf -v rev_arg " ${RUN_REV_FMT:-REV=%s}" "$rev"
    fi
    export GATES_RESUME_CMD="${RUN_CMD:-make $gate}$rev_arg"
    # The same zones `--check` will demand. Earning less than the check requires
    # would make the refusal unfixable: the command the refusal hands you would
    # never stamp the stage it named.
    _gate_zone_stages "$gate" "$repo_root" "$gates" "${rev:-HEAD}" >&2 || exit 1
    stages="$stages $_GATE_ZONE_STAGES"
    # Not `exec`: a green run has one more thing to say, and saying it needs the
    # markers the run has just written.
    local rc=0
    # shellcheck disable=SC2086
    bash "$gates" run "$@" $stages || rc=$?
    if [[ $rc -eq 0 ]]; then
        gate_next_note "$repo_root" "$gates" "$rev"
    fi
    exit "$rc"
}

# The thin gate's whole body. The check runner spawns a gate with no argv at
# all, so --check is the default.
gate_main() {
    local gate="$1" stages="$2"
    # `gate_exit` is several frames down and needs the name for its record.
    # A plain shell global, not AI_HATS_*: nobody configures this from outside.
    _ai_hats_gate_name="$gate"
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
        refuse)
            ai_hats_journal_catch "${_ai_hats_gate_name:-quality-gate}" refuse "$channel"
            [[ "$channel" == "checks" ]] && exit 2 || exit 1
            ;;
        *)
            printf 'gate_exit: unknown outcome %s\n' "$outcome" >&2
            exit 64
            ;;
    esac
}
