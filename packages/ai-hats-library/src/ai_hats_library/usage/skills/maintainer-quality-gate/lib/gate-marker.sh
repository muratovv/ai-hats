#!/usr/bin/env bash
# HATS-1137 / HATS-1601 / HATS-1614 — the pass-marker mechanism, shared by every
# gate here.
#
# A marker is a file named after a TREE under `<git-common-dir>/ai-hats/<gate>/`,
# recording the stage composition that earned it. Three properties follow.
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
# READ ACROSS GATES, WRITTEN PER GATE. The subset above is taken over what EVERY
# gate recorded for that tree, not over one gate's directory. Per-gate
# directories kept the write honest and made the read blind: `done-gate` running
# a superset of `merge-gate` on the same tree left `merge-gate` refusing, so
# absorption had never once fired. See `gate_marker_covers`.
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
#   gate_marker_root   <in_dir>                          -> the dir holding them all
#   gate_marker_dir    <gate> <in_dir>                   -> the gate's marker dir
#   gate_marker_path   <gate> <in_dir> <tree>            -> one marker's path
#   gate_marker_stages <gate> <in_dir> <tree>            -> what THAT gate recorded
#   gate_marker_covers <in_dir> <tree>                   -> what ANY gate recorded
#   gate_marker_ok     <in_dir> <tree> [stage...]        -> rc 0 iff those ran here
#   gate_marker_write  <gate> <in_dir> <tree> <stages> [line...] -> writes, prints path
#
# All return non-zero when the repo cannot be resolved. `<gate>` is a directory
# name: `e2e-gate` (pre-push, HATS-550/686), `done-gate` (HATS-1137) and
# `merge-gate` (HATS-1614) are the three consumers today. `<stages>` is one
# space-separated string.

# The dir every gate keeps its markers under. Named separately from
# `gate_marker_dir` because absorption reads ACROSS gates (HATS-1614).
gate_marker_root() {
    local in_dir="$1" common
    common="$( (cd "$in_dir" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null) || true)"
    [[ -z "$common" ]] && return 1
    # `git -C` makes --git-common-dir relative to in_dir; absolutise it.
    case "$common" in
        /*) : ;;
        *) common="$in_dir/$common" ;;
    esac
    common="$(cd "$common" 2>/dev/null && pwd)" || return 1
    printf '%s/ai-hats' "$common"
}

# Resolve the marker directory for one gate under the shared .git common dir.
gate_marker_dir() {
    local root
    root="$(gate_marker_root "$2")" || return 1
    printf '%s/%s' "$root" "$1"
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

# Every stage recorded for this tree, by ANY gate that ran on it.
#
# This is the absorption rule (ADR-0023 D5), and it lives in the READ. Markers
# stay one directory per gate, so provenance survives — which gate ran what is
# still on disk. What changed in HATS-1614 is that a lookup no longer stops at
# its own directory: a run of `done-gate` over tree T certifies the stages it
# names for T, and `merge-gate` asking about T is asking the same question of
# the same content. Keyed by gate instead, the two names would each demand their
# own run of an overlapping composition — the very double payment D5 exists to
# remove, and the reason absorption had never once fired.
#
# A marker only ever records stages a gate REALLY ran (`gate_stamp` writes it
# after a green run on a clean tree), so unioning cannot manufacture a pass.
gate_marker_covers() {
    local in_dir="$1" tree="$2"
    local root dir path out=''
    root="$(gate_marker_root "$in_dir")" || return 1
    for dir in "$root"/*/; do
        [[ -d "$dir" ]] || continue   # no gate dirs yet: the glob stayed literal
        path="$dir$tree"
        # A marker whose filename and recorded `tree=` disagree — half-written,
        # or copied by hand — names content it does not certify. It contributes
        # nothing rather than failing the whole read: another gate's honest
        # marker for the same tree still counts.
        [[ -f "$path" ]] || continue
        grep -qx "tree=$tree" "$path" 2>/dev/null || continue
        out="$out $(sed -n 's/^stages=//p' "$path" 2>/dev/null | head -1)"
    done
    printf '%s' "${out# }"
}

# rc 0 iff every demanded stage was run on this tree by some gate.
#
# No `<gate>` parameter, unlike every other function here: after HATS-1614 the
# lookup does not depend on which gate is asking, and keeping the argument would
# have implied it still did.
gate_marker_ok() {
    local in_dir="$1" tree="$2"
    shift 2
    # A gate that demands nothing is not a gate. Refusing here keeps a caller
    # whose composition came back empty from reading it as "everything ran".
    [[ $# -gt 0 ]] || return 1

    local recorded want have
    recorded=" $(gate_marker_covers "$in_dir" "$tree") " || return 1
    for want in ${@+"$@"}; do
        have="${recorded#*" $want "}"
        # No demanded stage may be absent: an unchanged string means no match,
        # so what ran on this tree certifies less than the gate asks for.
        [[ "$have" == "$recorded" ]] && return 1
    done
    return 0
}

# Days a marker may sit before the next write sweeps it (HATS-1682).
# Not an expiry: a marker cannot go stale, since a different tree is a different
# key. This is housekeeping — 125 files had accumulated on one checkout, the
# oldest naming a tree from a week nobody will return to.
: "${AI_HATS_GATE_MARKER_KEEP_DAYS:=30}"

# Drop markers older than the keep window. Best-effort: the sweep never decides
# a gate's verdict, so a failure here must not fail the run that earned one.
gate_marker_sweep() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    find "$dir" -type f -mtime "+${AI_HATS_GATE_MARKER_KEEP_DAYS}" -delete 2>/dev/null || true
}

# Write the marker for <tree> over <stages>; trailing args are provenance lines.
gate_marker_write() {
    local gate="$1" in_dir="$2" tree="$3" stages="$4"
    shift 4
    local dir
    dir="$(gate_marker_dir "$gate" "$in_dir")" || return 1
    mkdir -p "$dir" || return 1
    gate_marker_sweep "$dir"
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
