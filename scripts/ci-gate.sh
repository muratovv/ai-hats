#!/usr/bin/env bash
# The gate primitive: a REQUIREMENT over stages, and the EXECUTION that meets it.
#
# This script knows stages by name and nothing else — no gate names, no FSM
# edges, no roles, no exit code 2. Those belong to the two layers around it:
# `scripts/ci-local.sh` below it runs one stage; `scripts/gates.sh` above it
# says which stages a named gate demands and who binds it where.
#
#   ci-gate.sh check   [--rev <commit>] <stage>...
#       Which of these stages lack a green marker for the subject tree. Prints
#       the missing ones, one per line. rc 0 none missing, rc 1 some missing.
#       Runs NOTHING — this code path has no call to the stage runner.
#
#   ci-gate.sh run     [--rev <commit>] [--fresh] <stage>...
#       Run the stages that lack a marker, in the order given, stopping at the
#       first red; stamp each green one for the subject tree. `--fresh` ignores
#       existing markers. rc 0 when every named stage is marked; a red stage's
#       own rc otherwise.
#
#   ci-gate.sh subject [--rev <commit>]
#       Where a run would happen and on what: commit, tree, in-place or scratch.
#
# THE SUBJECT IS A COMMIT (default HEAD). A run happens in the current checkout
# only when it is clean AND its HEAD is that commit; otherwise in a one-shot
# scratch worktree pinned to the commit, torn down after the run. There is no
# flag for this. The invariant it holds: what the stages ran on IS what the
# marker names, and nothing merging into the checkout underneath can change it.
#
# MARKERS ARE PER STAGE: `<git-common-dir>/ai-hats/stages/<tree>/<stage>`. The
# common dir is shared by every worktree of a repo, so a stamp earned in a
# scratch checkout is visible from the main one. A gate is a set of stages, so
# "this gate passes" is "every file in the set exists" — and a run of a wider
# set never repeats what a narrower one already earned.
#
# Usage errors exit 64 (EX_USAGE); a primitive that cannot resolve its repo,
# subject or store exits 70 (EX_SOFTWARE). Neither is a verdict.

set -uo pipefail

# --- resolution ---------------------------------------------------------------

_die() {
    local rc="$1"
    shift
    printf '[ci-gate] %s\n' "$*" >&2
    exit "$rc"
}

_usage() {
    cat >&2 <<'EOF'
usage:
  ci-gate.sh check   [--rev <commit>] <stage>...
  ci-gate.sh run     [--rev <commit>] [--fresh] <stage>...
  ci-gate.sh subject [--rev <commit>]
EOF
    exit 64
}

# The checkout the command was issued from, and its shared git dir.
_repo_root() {
    git rev-parse --show-toplevel 2>/dev/null
}

_common_dir() {
    local root="$1" common
    common="$(git -C "$root" rev-parse --git-common-dir 2>/dev/null)" || return 1
    case "$common" in /*) : ;; *) common="$root/$common" ;; esac
    (cd "$common" 2>/dev/null && pwd)
}

_store() {
    local common
    common="$(_common_dir "$1")" || return 1
    printf '%s/ai-hats/stages' "$common"
}

_commit_of() {
    git -C "$1" rev-parse --verify --quiet "$2^{commit}" 2>/dev/null
}

_tree_of() {
    git -C "$1" rev-parse --verify --quiet "$2^{tree}" 2>/dev/null
}

_clean() {
    [[ -z "$(git -C "$1" status --porcelain 2>/dev/null)" ]]
}

# in-place iff clean and HEAD is the subject.
_where() {
    local root="$1" sha="$2" head
    head="$(_commit_of "$root" HEAD)" || { printf 'scratch'; return; }
    if [[ "$head" == "$sha" ]] && _clean "$root"; then
        printf 'in-place'
    else
        printf 'scratch'
    fi
}

# --- markers -----------------------------------------------------------------

# A marker counts only when its own `tree=` line agrees with its path: a
# half-written or hand-copied file names content it does not certify.
_marked() {
    local store="$1" tree="$2" stage="$3" path
    path="$store/$tree/$stage"
    [[ -f "$path" ]] && grep -qx "tree=$tree" "$path" 2>/dev/null
}

# Drop markers past the keep window. Housekeeping: it can revoke a pass, never
# grant one, and it never fails the run that earned a marker.
_sweep() {
    local store="$1" keep="${AI_HATS_GATE_MARKER_KEEP_DAYS:-30}"
    [[ -d "$store" ]] || return 0
    case "$keep" in
        *[!0-9]*)
            printf '[ci-gate] AI_HATS_GATE_MARKER_KEEP_DAYS=%s is not a whole number of days — sweeping at 30\n' \
                "$keep" >&2 || true
            keep=30
            ;;
    esac
    find "$store" -type f -mtime "+${keep}" -delete 2>/dev/null || true
    find "$store" -mindepth 1 -type d -empty -delete 2>/dev/null || true
    return 0
}

_stamp() {
    local store="$1" tree="$2" stage="$3" sha="$4" where="$5" tmp
    # Sweep FIRST: it deletes empty tree dirs, and the one made next is empty.
    _sweep "$store"
    mkdir -p "$store/$tree" || return 1
    tmp="$(mktemp "$store/$tree/.$stage.XXXXXX")" || return 1
    {
        printf 'tree=%s\n' "$tree"
        printf 'stage=%s\n' "$stage"
        printf 'commit=%s\n' "$sha"
        printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'ran=%s\n' "$where"
    } > "$tmp" && mv -f "$tmp" "$store/$tree/$stage"
}

# --- argv --------------------------------------------------------------------

REV='HEAD'
FRESH=''
STAGES=()

# Flags first, then bare stage names, and NOTHING after a stage name: a stage
# runs bare or not at all, because a marker for `unit -k foo` would be a lie.
_parse() {
    local allow_fresh="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rev)
                [[ -n "${2:-}" ]] || _die 64 "--rev names no commit"
                REV="$2"
                shift 2
                ;;
            --fresh)
                [[ -n "$allow_fresh" ]] || _die 64 "--fresh is a flag of 'run' only"
                FRESH=1
                shift
                ;;
            --*) _die 64 "unknown flag: $1" ;;
            *) break ;;
        esac
    done
    for arg in "$@"; do
        case "$arg" in
            -*) _die 64 "a stage runs bare — '$arg' after a stage name is not accepted" ;;
        esac
        STAGES+=("$arg")
    done
}

# --- the runner ---------------------------------------------------------------

# The stage runner is the SUBJECT tree's own `scripts/ci-local.sh`, so a commit
# that changes a stage is judged by its own definition. `CI_GATE_STAGE_RUNNER`
# overrides it — for a test that wants to count calls, not run suites.
_runner_in() {
    local checkout="$1"
    if [[ -n "${CI_GATE_STAGE_RUNNER:-}" ]]; then
        printf '%s' "$CI_GATE_STAGE_RUNNER"
    else
        printf '%s/scripts/ci-local.sh' "$checkout"
    fi
}

# Keep direct dispatcher and CI stage invocations serial; here, use the cores.
_export_pytest_addopts() {
    local py="${PYTHON:-python}"
    local addopts='--tb=line --no-header -p no:cacheprovider'
    if "$py" -m pytest -VV 2>/dev/null | grep -qi xdist; then
        local cores ceiling n
        cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null \
                 || nproc 2>/dev/null \
                 || sysctl -n hw.logicalcpu 2>/dev/null \
                 || echo 4)"
        [[ "$cores" =~ ^[0-9]+$ ]] || cores=4
        ceiling=8
        n=$(( cores < ceiling ? cores : ceiling ))
        (( n < 1 )) && n=1
        printf '[ci-gate] pytest-xdist detected — running -n%s --dist=loadgroup (cores=%s, cap=%s)\n' \
               "$n" "$cores" "$ceiling" >&2
        addopts="$addopts -n$n --dist=loadgroup"
    fi
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }$addopts"
}

# --- scratch checkout ---------------------------------------------------------
#
# Under the shared git dir, NOT $TMPDIR: macOS reaps the temp root by ACCESS
# time, and a uv-materialised venv arrives carrying the package cache's atime —
# born expired. The git dir is swept by nothing but us.
#
# Globals, not locals: the EXIT trap runs while the shell is already leaving,
# and a `rm -rf` is not a thing to bet on a name that lived inside a function.
_SCRATCH_REPO=''
_SCRATCH_DIR=''
_SCRATCH_CHECKOUT=''

_scratch_sweep() {
    [[ -n "$_SCRATCH_DIR" ]] || return 0
    if [[ -d "$_SCRATCH_CHECKOUT" ]]; then
        git -C "$_SCRATCH_REPO" worktree remove --force "$_SCRATCH_CHECKOUT" 2>/dev/null \
            || printf '[ci-gate] could not un-register %s — `git worktree prune` will\n' \
                      "$_SCRATCH_CHECKOUT" >&2
    fi
    rm -rf "$_SCRATCH_DIR" 2>/dev/null \
        || printf '[ci-gate] scratch dir left behind: %s\n' "$_SCRATCH_DIR" >&2
    return 0
}

# Mint a detached checkout of <sha> into `_SCRATCH_CHECKOUT`. Registers the
# sweep trap BEFORE `worktree add`, so a failed add still cleans up after
# itself. Sets globals rather than printing a path: called inside `$(...)` the
# trap would belong to the subshell and fire the moment it returned.
_scratch_checkout() {
    local root="$1" sha="$2" common
    common="$(_common_dir "$root")" || return 1
    mkdir -p "$common/ai-hats/gate-checkouts" || return 1
    _SCRATCH_REPO="$root"
    _SCRATCH_DIR="$(mktemp -d "$common/ai-hats/gate-checkouts/XXXXXXXX")" || return 1
    _SCRATCH_CHECKOUT="$_SCRATCH_DIR/tree"
    trap _scratch_sweep EXIT
    git -C "$root" worktree add --detach --quiet "$_SCRATCH_CHECKOUT" "$sha"
}

# --- verbs -------------------------------------------------------------------

cmd_check() {
    _parse '' "$@"
    [[ ${#STAGES[@]} -gt 0 ]] || _die 64 "check: no stage named"
    local root store sha tree stage missing=0
    root="$(_repo_root)" || _die 70 "not inside a git repository"
    store="$(_store "$root")" || _die 70 "cannot resolve the shared git dir of $root"
    sha="$(_commit_of "$root" "$REV")" || _die 70 "$REV names no commit in $root"
    tree="$(_tree_of "$root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    for stage in "${STAGES[@]}"; do
        if ! _marked "$store" "$tree" "$stage"; then
            printf '%s\n' "$stage"
            missing=1
        fi
    done
    return "$missing"
}

cmd_subject() {
    _parse '' "$@"
    [[ ${#STAGES[@]} -eq 0 ]] || _die 64 "subject takes no stage"
    local root sha tree
    root="$(_repo_root)" || _die 70 "not inside a git repository"
    sha="$(_commit_of "$root" "$REV")" || _die 70 "$REV names no commit in $root"
    tree="$(_tree_of "$root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    printf 'repo=%s\ncommit=%s\ntree=%s\nwhere=%s\n' "$root" "$sha" "$tree" "$(_where "$root" "$sha")"
}

cmd_run() {
    _parse 1 "$@"
    [[ ${#STAGES[@]} -gt 0 ]] || _die 64 "run: no stage named"
    local root store sha tree where checkout runner stage rc
    root="$(_repo_root)" || _die 70 "not inside a git repository"
    store="$(_store "$root")" || _die 70 "cannot resolve the shared git dir of $root"
    sha="$(_commit_of "$root" "$REV")" || _die 70 "$REV names no commit in $root"
    tree="$(_tree_of "$root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    where="$(_where "$root" "$sha")"

    if [[ "$where" == "in-place" ]]; then
        checkout="$root"
    else
        _scratch_checkout "$root" "$sha" \
            || _die 70 "could not check out $sha into a scratch worktree"
        checkout="$_SCRATCH_CHECKOUT"
        printf '[ci-gate] judging %s in a checkout of its own: %s\n' "$sha" "$checkout" >&2
        # A PYTHON naming another checkout's interpreter would import that
        # checkout's source while claiming to judge this commit.
        if [[ -n "${PYTHON:-}" && "$PYTHON" != "$checkout"/* ]]; then
            printf '[ci-gate] ignoring PYTHON=%s — it belongs to another checkout\n' "$PYTHON" >&2
            unset PYTHON
        fi
    fi
    runner="$(_runner_in "$checkout")"
    [[ -f "$runner" ]] || _die 70 "no stage runner at $runner"

    if [[ "$where" == "scratch" ]]; then
        # The project's own half: make the content runnable. A non-zero rc is
        # REPORTED and the run goes on — a stage failing for want of a
        # dependency says so loudly; skipping here would say nothing.
        if ! (cd "$checkout" && bash "$runner" --prepare); then
            printf '[ci-gate] the runner could not prepare %s (see above) — running anyway\n' \
                   "$checkout" >&2
        fi
    fi

    _export_pytest_addopts
    for stage in "${STAGES[@]}"; do
        if [[ -z "$FRESH" ]] && _marked "$store" "$tree" "$stage"; then
            printf '[ci-gate] %s: already green for tree %s — skipping\n' "$stage" "$tree" >&2
            continue
        fi
        printf '[ci-gate] %s: running in %s\n' "$stage" "$checkout" >&2
        (cd "$checkout" && bash "$runner" "$stage")
        rc=$?
        if [[ "$rc" -ne 0 ]]; then
            printf "[ci-gate] stage '%s' FAILED (rc=%s) — stopping here; earlier stamps stand\n" \
                   "$stage" "$rc" >&2
            exit "$rc"
        fi
        # A stage that changed tracked content ran the NEXT stages on something
        # other than the subject; the marker would then certify the wrong tree.
        if ! _clean "$checkout"; then
            printf "[ci-gate] stage '%s' left the tree dirty — NO marker for it, stopping\n" "$stage" >&2
            git -C "$checkout" status --porcelain >&2
            exit 1
        fi
        _stamp "$store" "$tree" "$stage" "$sha" "$where" \
            || _die 70 "green but the marker for $stage could not be written"
        printf '[ci-gate] %s: green — stamped for tree %s\n' "$stage" "$tree" >&2
    done
    printf '[ci-gate] every named stage is green for tree %s (%s)\n' "$tree" "$sha" >&2
    exit 0
}

case "${1:-}" in
    check) shift; cmd_check "$@" ;;
    run) shift; cmd_run "$@" ;;
    subject) shift; cmd_subject "$@" ;;
    *) _usage ;;
esac
