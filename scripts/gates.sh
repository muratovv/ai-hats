#!/usr/bin/env bash
# The named gates of this project — the ONE place a gate name appears in code.
#
# A gate is a name for a set of stages. This file holds the table that says
# which stages each gate requires and hands the set to `scripts/ci-gate.sh`,
# the primitive that checks markers and runs what is missing. WHERE a gate
# applies — which FSM edge, which worktree point — is not written here: that is
# the role's declaration (`composition.apps` in the role config), and the hook
# it binds learns the gate's name from that row's cargo.
#
#   gates.sh list                       # the roster, derived from the table
#   gates.sh stages <gate>              # the stages it requires, in run order
#   gates.sh check  <gate> [--rev C]    # = ci-gate.sh check [--rev C] <stages>
#   gates.sh run    <gate> [--rev C] [--fresh]
#   gates.sh table                      # the rows, for a renderer
#
# THE TABLE. One row per stage, in RUN ORDER (cheap first: the primitive stops
# at the first red, and a stale catalog is worth refusing before a suite runs).
# Column 2 names every gate that requires the stage; `-` is a stage no gate
# requires, listed so "named by no gate" is a decision a reader can see rather
# than a row somebody forgot. Column 3 is what the stage checks — the sentence
# the ADR's stage table renders from, so it rots here or nowhere.
#
# Every stage named here is a `ci_*` function in `scripts/ci-local.sh`, and
# every `ci_*` function has a row here: `tests/test_gates_table.py` refuses
# both directions. `review-gate` and `merge-gate` name the same set on purpose
# — they differ in the edge they sit on, not in what they demand — and both
# stay inside `done-gate`, so one run of the widest pays for all three.
#
# Usage errors exit 64. Not a checks-channel hook: no exit code 2 here.

set -uo pipefail

gate_table() {
    cat <<'TABLE'
e2e-catalog      | review-gate merge-gate done-gate push-gate | tests/e2e/CATALOG.md matches the flow blocks in the tests' docstrings
lint             | review-gate merge-gate done-gate push-gate | ruff check and ruff format --check, both over the whole tree
shellcheck       | review-gate merge-gate done-gate           | every tracked *.sh is clean at severity warning and above
dependency-floor | review-gate merge-gate done-gate           | every pin on a workspace package tracks that package's version
silent-fallback  | review-gate merge-gate done-gate           | no broad except swallows a failure without reporting it
test-isolation   | review-gate merge-gate done-gate           | the suite patches its own units no more than the recorded baseline
prose-refs       | review-gate merge-gate done-gate push-gate | paths, library prefixes, sections and symbols named in library prose resolve
ticket-ids       | review-gate merge-gate done-gate push-gate | no tracker id in shipped library prose
env-reference    | review-gate merge-gate done-gate push-gate | docs/reference-env.md matches the env declarations the code reads
adr-integrity    | push-gate                                  | every ADR citation resolves and a number names exactly one file
bidi             | push-gate                                  | no bidirectional control characters, which are invisible in review
wheel-contents   | review-gate merge-gate done-gate           | every tracked src file reaches the wheel built through the sdist
master-ci        | done-gate                                  | master's last CI verdict is green (network)
unit             | review-gate merge-gate done-gate push-gate | every test not marked integration
integration      | done-gate                                  | the real-subprocess tests outside tests/e2e
merge-smoke      | done-gate                                  | the curated smoke subset of tests/e2e
e2e              | push-gate                                  | the full tier: integration or smoke, quarantine and live agents excluded
coverage         | -                                          | the tests outside tests/e2e in one process, at the coverage floor (CI)
security         | -                                          | pip-audit over the interpreter's whole environment (CI-authoritative)
version-skew     | -                                          | every workspace package is ahead of what PyPI has (network)
python-pin       | -                                          | every copy of the Python pin agrees and CI runs it
tmp-sweep        | -                                          | housekeeping: reap dead test cruft from TMPDIR; it can fail nothing
prepare          | -                                          | precondition: a venv for this checkout; it asserts nothing
TABLE
}

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CI_GATE="$_self_dir/ci-gate.sh"

_die() {
    local rc="$1"
    shift
    printf '[gates] %s\n' "$*" >&2
    exit "$rc"
}

_trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Every gate the table names, first appearance first, `-` excluded.
gates_list() {
    local stage gates _desc token seen=' ' out=''
    while IFS='|' read -r stage gates _desc; do
        for token in $gates; do
            [[ "$token" == "-" ]] && continue
            case "$seen" in *" $token "*) continue ;; esac
            seen="$seen$token "
            out="$out $token"
        done
    done < <(gate_table)
    printf '%s\n' "${out# }"
}

# The stages a gate requires, in table order, one line. rc 1 for a name the
# table does not know.
gates_stages() {
    local want="$1" stage gates _desc token out='' known=''
    while IFS='|' read -r stage gates _desc; do
        for token in $gates; do
            if [[ "$token" == "$want" ]]; then
                out="$out $(_trim "$stage")"
                known=1
            fi
        done
    done < <(gate_table)
    [[ -n "$known" ]] || return 1
    printf '%s\n' "${out# }"
}

_require_gate() {
    local gate="${1:-}"
    [[ -n "$gate" ]] || _die 64 "no gate named (gates: $(gates_list))"
    gates_stages "$gate" >/dev/null || _die 64 "no such gate: $gate (gates: $(gates_list))"
}

# Hand the set to the primitive. Flags travel through untouched; a stage name
# on the command line is refused — the table decides what a gate runs.
_delegate() {
    local verb="$1" gate="$2"
    shift 2
    local stages
    set -- "$@"
    local i=1 arg
    while [[ $i -le $# ]]; do
        arg="${!i}"
        case "$arg" in
            --rev) i=$((i + 2)) ;;
            --fresh) i=$((i + 1)) ;;
            *) _die 64 "'$arg': a gate's stages come from the table, not the command line" ;;
        esac
    done
    [[ -f "$CI_GATE" ]] || _die 70 "no primitive at $CI_GATE"
    stages="$(gates_stages "$gate")"
    # The set is a space-separated list by contract.
    # shellcheck disable=SC2086
    exec bash "$CI_GATE" "$verb" "$@" $stages
}

case "${1:-}" in
    list) gates_list ;;
    table) gate_table ;;
    stages)
        _require_gate "${2:-}"
        gates_stages "$2"
        ;;
    check | run)
        _require_gate "${2:-}"
        verb="$1"
        gate="$2"
        shift 2
        _delegate "$verb" "$gate" "$@"
        ;;
    *)
        cat >&2 <<'EOF'
usage:
  gates.sh list
  gates.sh stages <gate>
  gates.sh check  <gate> [--rev <commit>]
  gates.sh run    <gate> [--rev <commit>] [--fresh]
  gates.sh table
EOF
        exit 64
        ;;
esac
