#!/usr/bin/env bash
# HATS-1137 / HATS-1601 — the pass-marker mechanism, shared by every gate here.
#
# A marker is a file named after a TREE under `<git-common-dir>/ai-hats/<gate>/`,
# recording the stage composition that earned it. Two properties follow.
#
# KEYED BY TREE, not by commit. `_fast_forward_merge` merges --no-ff
# (manager.py:2266), so a merge commit's sha is always new while its tree is
# byte-identical to the branch's in 18 of the last 20 merges — keyed by commit,
# the maintainer paid twice for one tree. Staleness is still impossible: a
# different tree is a different key, and the old marker stops applying.
#
# CARRYING ITS COMPOSITION. `gate_marker_ok` passes only when the required
# stages are a SUBSET of the recorded ones. That one comparison does two jobs:
# adding a stage invalidates every marker that never ran it, and a fuller run
# covers the gates it contains (absorption, ADR-0023 D5).
#
# `--git-common-dir`, never `--git-dir`: the common dir is shared by every
# worktree of a repo, which is what makes a marker WRITTEN inside a linked
# worktree visible FROM the main checkout. The review→done gate depends on
# exactly that — the run happens in the task worktree, the check runs in main.
#
# Sourced, never executed. Every function takes the gate name and a directory
# inside the repo explicitly; there is no ambient default, because a gate that
# silently resolved the wrong repo would answer confidently about the wrong
# content. Callers keep their own `set` options — nothing here changes them.
#
#   gate_marker_dir   <gate> <in_dir>                    -> the gate's marker dir
#   gate_marker_path  <gate> <in_dir> <tree>             -> one marker's path
#   gate_marker_ok    <gate> <in_dir> <tree> [stage...]  -> rc 0 iff it covers them
#   gate_marker_write <gate> <in_dir> <tree> <stages> [line...] -> writes, prints path
#
# All four return non-zero when the repo cannot be resolved. `<gate>` is a
# directory name: `e2e-gate` (pre-push, HATS-550/686) and `done-gate` (HATS-1137)
# are the two consumers today. `<stages>` is one space-separated string.

# Resolve the marker directory for one gate under the shared .git common dir.
gate_marker_dir() {
    local gate="$1" in_dir="$2"
    local common
    common="$( (cd "$in_dir" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null) || true)"
    [[ -z "$common" ]] && return 1
    # `git -C` makes --git-common-dir relative to in_dir; absolutise it.
    case "$common" in
        /*) : ;;
        *) common="$in_dir/$common" ;;
    esac
    common="$(cd "$common" 2>/dev/null && pwd)" || return 1
    printf '%s/ai-hats/%s' "$common" "$gate"
}

gate_marker_path() {
    local dir
    dir="$(gate_marker_dir "$1" "$2")" || return 1
    printf '%s/%s' "$dir" "$3"
}

# Print the stages a marker records, empty when it declares none.
gate_marker_stages() {
    local path
    path="$(gate_marker_path "$1" "$2" "$3")" || return 1
    [[ -f "$path" ]] || return 1
    sed -n 's/^stages=//p' "$path" 2>/dev/null | head -1
}

# A marker counts only when its filename and its recorded `tree=` agree — a
# half-written or hand-copied file names content it does not certify — AND when
# every stage the caller demands is one it actually ran.
gate_marker_ok() {
    local gate="$1" in_dir="$2" tree="$3"
    shift 3
    local path
    path="$(gate_marker_path "$gate" "$in_dir" "$tree")" || return 1
    [[ -f "$path" ]] || return 1
    grep -qx "tree=$tree" "$path" 2>/dev/null || return 1

    local recorded want have
    recorded=" $(gate_marker_stages "$gate" "$in_dir" "$tree") "
    for want in ${@+"$@"}; do
        have="${recorded#*" $want "}"
        # No demanded stage may be absent: an unchanged string means no match,
        # so this marker certifies less than the gate asks for.
        [[ "$have" == "$recorded" ]] && return 1
    done
    return 0
}

# Write the marker for <tree> over <stages>; trailing args are provenance lines.
gate_marker_write() {
    local gate="$1" in_dir="$2" tree="$3" stages="$4"
    shift 4
    local dir
    dir="$(gate_marker_dir "$gate" "$in_dir")" || return 1
    mkdir -p "$dir" || return 1
    {
        printf 'tree=%s\n' "$tree"
        printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'stages=%s\n' "$stages"
        # bash 3.2 + `set -u`: guard the expansion, an empty "$@" is the norm.
        local line
        for line in ${@+"$@"}; do
            printf '%s\n' "$line"
        done
    } > "$dir/$tree" || return 1
    printf '%s/%s' "$dir" "$tree"
}
