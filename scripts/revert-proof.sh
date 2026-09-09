#!/usr/bin/env bash
# HATS-1899 — prove a test fails when the fix it guards is reverted.
#
# The `done`-gate expects it, and for the install tier it cannot be done in the
# working tree at all: that tier only sees COMMITTED content, so the loop is
# temp-commit -> run -> reset. Done by hand it is tedious and it points a
# `reset --hard` at whatever directory you happen to stand in; the third such
# run by hand is where this card's own incident came from.
#
# Usage:
#   scripts/revert-proof.sh [--from <ref>] <path>... -- <pytest node id>...
#
# Exits 0 only when the tests are GREEN before the revert and RED after. The
# green half is the positive control: without it a red run proves nothing, since
# a test that never passed is red for its own reasons.
set -euo pipefail

die() {
    printf 'revert-proof: %s\n' "$1" >&2
    exit 2
}

from_ref="HEAD~1"
paths=()
nodes=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)
            [[ $# -ge 2 ]] || die "--from needs a ref"
            from_ref="$2"
            shift 2
            ;;
        --)
            shift
            nodes=("$@")
            break
            ;;
        *)
            paths+=("$1")
            shift
            ;;
    esac
done

[[ ${#paths[@]} -gt 0 ]] || die "no path to revert; usage: $0 [--from <ref>] <path>... -- <node id>..."
[[ ${#nodes[@]} -gt 0 ]] || die "no pytest node id after --; a revert with nothing to run proves nothing"

# Only ever inside a linked worktree. The reset below is exactly the command
# that moved master by a commit when it ran in the main checkout.
git_dir="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)" || die "not a git tree"
common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
[[ "$git_dir" != "$common_dir" ]] || die "refusing to run in the MAIN checkout — stand in a task worktree"

[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty; commit or clean it first"

for path in "${paths[@]}"; do
    [[ -e "$path" ]] || die "no such path: $path"
done

runner=(python3 -m pytest)
[[ -x ./.venv/bin/python ]] && runner=(./.venv/bin/python -m pytest)

head_sha="$(git rev-parse HEAD)"
restored=0
restore() {
    [[ $restored -eq 1 ]] && return 0
    restored=1
    git reset --hard --quiet "$head_sha"
    printf 'revert-proof: restored HEAD %s\n' "${head_sha:0:8}" >&2
}
# A trap, not a trailing line: an interrupted run must still put HEAD back.
trap restore EXIT INT TERM

printf 'revert-proof: [1/2] the tests as they stand — expecting GREEN\n' >&2
if "${runner[@]}" "${nodes[@]}"; then
    before="green"
else
    before="red"
fi

if [[ "$before" != "green" ]]; then
    printf 'revert-proof: VERDICT before=red — a test that does not pass now cannot prove anything by failing later\n' >&2
    exit 1
fi

printf 'revert-proof: [2/2] reverting %s from %s, then running again — expecting RED\n' \
    "${paths[*]}" "$from_ref" >&2
git checkout "$from_ref" -- "${paths[@]}"
# The install tier reads committed content only, so the revert has to be a commit.
git commit --no-verify --quiet -m "revert-proof: temporary revert of ${paths[*]}"

if "${runner[@]}" "${nodes[@]}"; then
    after="green"
else
    after="red"
fi

restore

printf 'revert-proof: VERDICT before=%s after=%s\n' "$before" "$after" >&2
if [[ "$after" == "red" ]]; then
    printf 'revert-proof: the fix is covered — reverting it turns the named tests red\n' >&2
    exit 0
fi
printf 'revert-proof: NOT COVERED — the tests stayed green without the fix\n' >&2
exit 1
