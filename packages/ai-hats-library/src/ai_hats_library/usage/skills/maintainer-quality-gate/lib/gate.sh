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
