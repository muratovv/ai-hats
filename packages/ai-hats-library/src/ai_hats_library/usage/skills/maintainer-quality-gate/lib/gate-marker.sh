#!/usr/bin/env bash
# HATS-1137 — the SHA pass-marker mechanism, shared by every gate in this skill.
#
# A marker is a file named after a commit SHA under
# `<git-common-dir>/ai-hats/<gate>/`. Its key is the CONTENT it certifies, so it
# cannot go stale by construction: a new commit is a new SHA, and the old marker
# simply stops applying. There is no invalidation step and none is needed.
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
#   gate_marker_dir   <gate> <in_dir>              -> prints the gate's marker dir
#   gate_marker_path  <gate> <in_dir> <sha>        -> prints one marker's path
#   gate_marker_ok    <gate> <in_dir> <sha>        -> rc 0 iff a valid marker exists
#   gate_marker_write <gate> <in_dir> <sha> [line...] -> writes it, prints its path
#
# All four return non-zero when the repo cannot be resolved. `<gate>` is a
# directory name: `e2e-gate` (pre-push, HATS-550/686) and `done-gate` (HATS-1137)
# are the two consumers today.

# Resolve the marker directory for one gate under the shared .git common dir.
gate_marker_dir() {
    local gate="$1" in_dir="$2"
    local common
    common="$(git -C "$in_dir" rev-parse --git-common-dir 2>/dev/null || true)"
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

# A marker counts only when its filename and its recorded `sha=` agree — a
# half-written or hand-copied file names a commit it does not certify.
gate_marker_ok() {
    local path
    path="$(gate_marker_path "$1" "$2" "$3")" || return 1
    [[ -f "$path" ]] || return 1
    grep -qx "sha=$3" "$path" 2>/dev/null
}

# Write the marker for <sha>; trailing args become extra provenance lines.
gate_marker_write() {
    local gate="$1" in_dir="$2" sha="$3"
    shift 3
    local dir
    dir="$(gate_marker_dir "$gate" "$in_dir")" || return 1
    mkdir -p "$dir" || return 1
    {
        printf 'sha=%s\n' "$sha"
        printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        # bash 3.2 + `set -u`: guard the expansion, an empty "$@" is the norm.
        local line
        for line in ${@+"$@"}; do
            printf '%s\n' "$line"
        done
    } > "$dir/$sha" || return 1
    printf '%s/%s' "$dir" "$sha"
}
