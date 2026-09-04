#!/usr/bin/env bash
# The stages — every kind of check this repository runs — and how to earn them.
#
# One file, two halves. The first half is the STAGES: what each one runs, as a
# `ci_<stage>` function. CI calls one per job, `all` is the local bundle, and
# because CI and every gate share THIS file their commands cannot drift apart.
# The second half is the MARKER machinery: `check` asks which of a list of
# stages lack a green marker for a tree, `run` earns the missing ones.
#
# No gate name appears here. A gate is a thin script that declares the stages
# it requires and hands the list to this one — see the quality-gate skill's
# `hooks/*-gate.sh`.
#
#   gates.sh list                       # stage | what it checks
#   gates.sh <stage>                    # run ONE stage, bare — what CI calls
#   gates.sh all                        # the local bundle; earns no marker
#   gates.sh --prepare                  # a venv for this checkout (a precondition)
#   gates.sh check [--rev C] <stage>... # which of these lack a marker; runs nothing
#   gates.sh run   [--rev C] [--fresh] <stage>...   # run the unmarked, stamp each
#   gates.sh subject [--rev C]          # what a run would judge, and where
#
# A STAGE RUNS BARE: nothing after its name reaches pytest, because a marker
# earned for `unit -k foo` would be a lie. CI's parallelism rides PYTEST_ADDOPTS.
#
# THE SUBJECT IS A COMMIT (default HEAD) and its tree is what a marker names. A
# run happens in this checkout only when it is clean AND its HEAD is that
# commit; otherwise in a one-shot scratch checkout pinned to the commit. There is
# no flag for it: what the stages ran on IS what the marker names, and nothing
# merging into the checkout underneath can change it.
#
# MARKERS ARE PER STAGE: `<git-common-dir>/ai-hats/stages/<tree>/<stage>`. The
# common dir is shared by every worktree, so a stamp from a scratch checkout is
# visible from the main one. "This gate passes" is "every file in its set
# exists", so a wider gate never repeats what a narrower one earned.
#
# Non-unit tools run as `python -m <tool>` so one line works in CI and locally;
# override the interpreter with PYTHON=/path/to/python.
#
# NOTE: `install-smoke` is deliberately NOT a stage — it writes ~/.local/bin on
# a dev box. NOTE: `security` (pip-audit) is EXCLUDED from `all` — it audits the
# interpreter's whole environment, so a polluted dev venv reports CVEs ai-hats
# never declares; CI is authoritative.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "[gates] not inside a git repository" >&2
    exit 70
}
cd "$repo_root"

if [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python" ]]; then
    PY="$repo_root/.venv/bin/python"
elif [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python3" ]]; then
    PY="$repo_root/.venv/bin/python3"
else
    PY="${PYTHON:-python}"
fi

# ===========================================================================
# THE STAGES
# ===========================================================================

# What each stage checks, one line — the sentence ADR-0023's stage table
# renders from, so it rots here or nowhere. Every `ci_*` function below has a
# row and every row a function (`tests/test_gates_table.py` holds both ways).
stage_table() {
    cat <<'TABLE'
e2e-catalog      | tests/e2e/CATALOG.md matches the flow blocks in the tests' docstrings
lint             | ruff check and ruff format --check, both over the whole tree
shellcheck       | every tracked *.sh is clean at severity warning and above
dependency-floor | every pin on a workspace package tracks that package's version
silent-fallback  | no broad except swallows a failure without reporting it
test-isolation   | the suite patches its own units no more than the recorded baseline
prose-refs       | paths, library prefixes, sections and symbols named in library prose resolve
ticket-ids       | no tracker id in shipped library prose
env-reference    | docs/reference-env.md matches the env declarations the code reads
gate-table       | ADR-0023's stage and gate tables match this file and the gates
adr-integrity    | every ADR citation resolves and a number names exactly one file
bidi             | no bidirectional control characters, which are invisible in review
wheel-contents   | every tracked src file reaches the wheel built through the sdist
master-ci        | master's last CI verdict is green (network)
unit             | every test not marked integration
integration      | the real-subprocess tests outside tests/e2e
merge-smoke      | the curated smoke subset of tests/e2e
e2e              | the full tier: integration or smoke, quarantine and live agents excluded
coverage         | the tests outside tests/e2e in one process, at the coverage floor (CI)
security         | pip-audit over the interpreter's whole environment (CI-authoritative)
version-skew     | every workspace package is ahead of what PyPI has (network)
python-pin       | every copy of the Python pin agrees and CI runs it
tmp-sweep        | housekeeping: reap dead test cruft from TMPDIR; it can fail nothing
prepare          | precondition: a venv for this checkout; it asserts nothing
TABLE
}

# A stage that could NOT RUN is not a stage that refused. Python exits 1 on an
# uncaught ModuleNotFoundError exactly as a check exits 1 on a finding, so the
# code alone cannot tell the two apart. Only the fast checkers route through
# here; the pytest tiers stream for minutes, and a missing pytest is loud.
run_py() {
    local err rc=0 missing
    err="$(mktemp "${TMPDIR:-/tmp}/gates-stage.XXXXXX")" || err=''
    if [[ -z "$err" ]]; then
        "$PY" "$@"
        return
    fi
    "$PY" "$@" 2>"$err" || rc=$?
    cat "$err" >&2
    missing="$(sed -n "s/^ModuleNotFoundError: No module named '\([^']*\)'.*/\1/p" "$err" | tail -1)"
    rm -f "$err"
    if [[ $rc -ne 0 && -n "$missing" ]]; then
        echo "[gates] BROKEN: this stage never ran — no module named '$missing'" >&2
        echo "  Nothing above is a finding. Install '$missing' for $PY, then re-run." >&2
        return 3
    fi
    return $rc
}

ci_tmp_sweep() {
    # Housekeeping, not a check: in the `all` bundle and in NO gate, because a
    # gate names what must be green and this can only free space. Runs FIRST so
    # the heavy stages get the space, and never fails the bundle.
    local sweep="$repo_root/scripts/clean-tmp-cruft.sh"
    [[ -x "$sweep" ]] || return 0
    echo "[gates] tmp-sweep (reap provably-dead test cruft)" >&2
    bash "$sweep" || echo "[gates] tmp-sweep left dirs behind (see above); continuing" >&2
}

ci_lint() {
    echo "[gates] lint (ruff check + format)" >&2
    run_py -m ruff check .
    # The same `.` for both: two scopes could not stay equal by convention, and
    # ruff's own extend-exclude is the one that should decide (HATS-1651).
    run_py -m ruff format --check .
}

ci_unit() {
    echo "[gates] unit (pytest -m 'not integration')" >&2
    env \
        -u VIRTUAL_ENV \
        -u VIRTUAL_ENV_PROMPT \
        -u PYTHONPATH \
        -u PYTHONHOME \
        -u PYTHONUSERBASE \
        uv run --isolated --no-project --python "$PY" --with-editable ".[dev]" \
        python -B -m pytest -m "not integration" -q
}

# The integration tier OUTSIDE tests/e2e — the half `unit` excludes by marker
# and `merge-smoke` does not reach by path.
ci_integration() {
    echo "[gates] integration (pytest --ignore=tests/e2e -m integration)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e -m integration -q
}

ci_coverage() {
    echo "[gates] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e/ \
        --cov=ai_hats \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=78 \
        -q
}

ci_security() {
    # ruff's `S` family covers bandit's inventory on every road; the network
    # half is what is left here.
    echo "[gates] security (pip-audit)" >&2
    run_py -m pip_audit
}

ci_merge_smoke() {
    echo "[gates] merge-smoke (curated e2e subset)" >&2
    "$PY" -B -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q
}

ci_dependency_floor() {
    echo "[gates] dependency-floor (pins vs workspace versions)" >&2
    run_py scripts/check_dependency_floor.py
}

# A ratchet: green only while the tree patches its own units no more than the
# recorded baseline.
ci_test_isolation() {
    echo "[gates] test-isolation (patching of code under test vs the baseline)" >&2
    run_py scripts/check_test_isolation.py
}

ci_bidi() {
    echo "[gates] bidi (bidirectional controls, invisible in review)" >&2
    run_py scripts/check_bidi.py
}

ci_python_pin() {
    echo "[gates] python-pin (every copy of the pin agrees; CI runs it)" >&2
    run_py scripts/check_python_pin.py
}

ci_silent_fallback() {
    echo "[gates] silent-fallback (broad handlers nothing can escape from)" >&2
    run_py scripts/check_silent_fallback.py
}

# The flow catalog is rendered from the tests' own docstrings, so it goes stale
# the moment one is edited without regenerating.
ci_e2e_catalog() {
    echo "[gates] e2e-catalog (tests/e2e/CATALOG.md vs the flow blocks)" >&2
    run_py scripts/gen_e2e_catalog.py --check
}

# Prose carries no assert, so a citation into an ADR rots green.
ci_adr_integrity() {
    echo "[gates] adr-integrity (ADR citations resolve; a number names one file)" >&2
    run_py scripts/check_adr_integrity.py
}

# The same gate aimed at the library's own references, where 21 had rotted.
ci_prose_refs() {
    echo "[gates] prose-refs (paths, library prefixes, sections and symbols in library prose)" >&2
    run_py scripts/check_prose_refs.py
}

# What prose must NOT carry: the library installs into other projects, where
# this repo's tracker ids are dead links. It reports the ids it still finds in
# docs/adr and CHANGELOG, so a clean run proves the pattern is alive.
ci_ticket_ids() {
    echo "[gates] ticket-ids (no tracker id in shipped library prose)" >&2
    run_py scripts/check_no_ticket_ids.py
}

# The env reference page is rendered from the declarations the code reads. A
# stale NUMBER is worse than no page: prose invites a check, a number trust.
ci_env_reference() {
    echo "[gates] env-reference (docs/reference-env.md vs the env declarations)" >&2
    run_py scripts/gen_env_reference.py --check
}

# ADR-0023's inventory of stages and gates renders from this file and the gate
# scripts, so it goes stale the moment a row is added without regenerating —
# which is how seven of twenty-three stages came to be named nowhere in it.
ci_gate_table() {
    echo "[gates] gate-table (ADR-0023's stage and gate tables vs this file and the gates)" >&2
    run_py scripts/gen_gate_table.py --check
}

# The full maintainer tier (the slow one). Excluded from `all`; the push gate's
# selection, kept here so `make e2e` cannot mean something narrower.
ci_e2e() {
    echo "[gates] e2e (integration + smoke, quarantine and live agy excluded)" >&2
    "$PY" -B -m pytest -m "(integration or smoke) and not quarantine and not live_agy" tests/e2e/ tests/smoke/ -q
}

# Make THIS checkout runnable, so `$PY` resolves to an interpreter that imports
# this tree and not another one. NOT a stage: it asserts nothing. Asked before a
# run inside a scratch checkout, which has no `.venv` at all; the hook it
# delegates to is the one every task worktree gets, and a usable `.venv` makes
# it a no-op.
ci_prepare() {
    echo "[gates] prepare (a venv for this checkout, if it needs one)" >&2
    local hook="$repo_root/packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/worktree-venv/hooks/provision-venv.sh"
    if [[ ! -f "$hook" ]]; then
        echo "[gates] no provision-venv hook at $hook — nothing to prepare" >&2
        return 1
    fi
    AI_HATS_WORKTREE_PATH="$repo_root" bash "$hook"
}

# NOTE: excluded from `all` — it queries PyPI, so an offline dev box would fail
# a legitimate push. CI is authoritative; SKEW_BASE=<sha> to reproduce.
ci_version_skew() {
    echo "[gates] version-skew (workspace pkgs ahead of PyPI)" >&2
    run_py scripts/check_pkg_version_skew.py "${SKEW_BASE:-origin/master}"
}

# The shell this repo SHIPS is the half no python linter ever saw: `git_hooks/**`
# and the skills' `hooks/**` run in a consuming project, where a portability bug
# is a silent skip. Severity capped at `warning` on purpose: `info` and below is
# 41 findings, twenty of them SC1091 on dynamically sourced libs.
ci_shellcheck() {
    echo "[gates] shellcheck (tracked *.sh, severity >= warning)" >&2
    if ! command -v shellcheck >/dev/null 2>&1; then
        echo "[shellcheck] SKIPPED: not installed — no shell script was checked." >&2
        return 0
    fi
    local count
    count="$(git ls-files -- '*.sh' | wc -l | tr -d ' ')"
    if [[ "$count" -eq 0 ]]; then
        echo "[shellcheck] no tracked *.sh — nothing was checked." >&2
        return 0
    fi
    git ls-files -z -- '*.sh' | xargs -0 shellcheck -S warning
    echo "[shellcheck] ok: $count file(s) clean" >&2
}

# Offline given a warm uv cache, ~3s for all five packages. The only check here
# that judges the ARTEFACT rather than the tree.
ci_wheel_contents() {
    echo "[gates] wheel-contents (tracked src files vs the sdist-chained wheel)" >&2
    run_py scripts/check_wheel_contents.py
}

# NETWORK, like `version-skew`, so excluded from `all` and from CI (where
# master's own verdict is circular). The close is the moment a human is looking.
ci_master_ci() {
    echo "[gates] master-ci (master's last CI verdict)" >&2
    run_py scripts/check_master_ci.py
}

# The stages that run pytest: `run` exports PYTEST_ADDOPTS — and says what it
# found — before the first of these it reaches, so a checker-only run stays
# silent about xdist. Hand-kept; `tests/test_gates_table.py` holds it equal to
# the functions above that invoke pytest.
PYTEST_STAGES='unit integration coverage merge-smoke e2e'

_is_pytest_stage() {
    case " $PYTEST_STAGES " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}

# The stage set IS the set of `ci_*` functions above: the dispatch and the usage
# line both read it, so a stage cannot be reachable and unlisted or listed and
# unreachable. A helper that is not a stage does not take the prefix.
known_stages() {
    declare -F | sed -n 's/^declare -f ci_//p' | tr '_' '-' | sort
}

# ===========================================================================
# MARKERS: check (a requirement) and run (an execution)
# ===========================================================================

_die() {
    local rc="$1"
    shift
    printf '[gates] %s\n' "$*" >&2
    exit "$rc"
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

# A marker counts only when its own `tree=` line agrees with its path: a
# half-written or hand-copied file names content it does not certify.
_marked() {
    local store="$1" tree="$2" stage="$3" path
    path="$store/$tree/$stage"
    [[ -f "$path" ]] && grep -qx "tree=$tree" "$path" 2>/dev/null
}

# Drop markers past the keep window. Housekeeping: it can revoke a pass, never
# grant one, and never fails the run that earned a marker.
_sweep() {
    local store="$1" keep="${AI_HATS_GATE_MARKER_KEEP_DAYS:-30}"
    [[ -d "$store" ]] || return 0
    case "$keep" in
        *[!0-9]*)
            printf '[gates] AI_HATS_GATE_MARKER_KEEP_DAYS=%s is not a whole number of days — sweeping at 30\n' \
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

REV='HEAD'
FRESH=''
STAGES=()

# Flags first, then bare stage names, and NOTHING after a stage name.
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
    local arg
    for arg in "$@"; do
        case "$arg" in
            -*) _die 64 "a stage runs bare — '$arg' after a stage name is not accepted" ;;
        esac
        STAGES+=("$arg")
    done
}

# The stage runner is the SUBJECT tree's own copy of this script, so a commit
# that changes a stage is judged by its own definition. `GATES_STAGE_RUNNER`
# overrides it — for a test that wants to count calls, not run suites.
_runner_in() {
    local checkout="$1"
    if [[ -n "${GATES_STAGE_RUNNER:-}" ]]; then
        printf '%s' "$GATES_STAGE_RUNNER"
    else
        printf '%s/scripts/gates.sh' "$checkout"
    fi
}

# The pytest the stages will run: the checkout's own venv first, then PYTHON,
# then PATH. Probing another interpreter than the one that runs would answer
# the xdist question for the wrong pytest.
_pytest_probe() {
    local checkout="$1"
    if [[ -x "$checkout/.venv/bin/python" ]]; then
        "$checkout/.venv/bin/python" -m pytest -VV 2>/dev/null
    elif [[ -n "${PYTHON:-}" ]]; then
        "$PYTHON" -m pytest -VV 2>/dev/null
    elif command -v pytest >/dev/null 2>&1; then
        pytest -VV 2>/dev/null
    else
        return 1
    fi
}

# Bare stage invocations stay serial; a run uses the cores.
_export_pytest_addopts() {
    local checkout="$1"
    local addopts='--tb=line --no-header -p no:cacheprovider --disable-warnings'
    if _pytest_probe "$checkout" | grep -qi xdist; then
        local cores ceiling n
        cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null \
                 || nproc 2>/dev/null \
                 || sysctl -n hw.logicalcpu 2>/dev/null \
                 || echo 4)"
        [[ "$cores" =~ ^[0-9]+$ ]] || cores=4
        ceiling=8
        n=$(( cores < ceiling ? cores : ceiling ))
        (( n < 1 )) && n=1
        printf '[gates] pytest-xdist detected — running -n%s --dist=loadgroup (cores=%s, cap=%s)\n' \
               "$n" "$cores" "$ceiling" >&2
        addopts="$addopts -n$n --dist=loadgroup"
    fi
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }$addopts"
}

# Under the shared git dir, NOT $TMPDIR: macOS reaps the temp root by ACCESS
# time, and a uv-materialised venv arrives carrying the package cache's atime —
# born expired. Globals, not locals: the EXIT trap runs while the shell is
# already leaving.
_SCRATCH_REPO=''
_SCRATCH_DIR=''
_SCRATCH_CHECKOUT=''

_scratch_sweep() {
    [[ -n "$_SCRATCH_DIR" ]] || return 0
    if [[ -d "$_SCRATCH_CHECKOUT" ]]; then
        git -C "$_SCRATCH_REPO" worktree remove --force "$_SCRATCH_CHECKOUT" 2>/dev/null \
            || printf '[gates] could not un-register %s — `git worktree prune` will\n' \
                      "$_SCRATCH_CHECKOUT" >&2
    fi
    rm -rf "$_SCRATCH_DIR" 2>/dev/null \
        || printf '[gates] scratch dir left behind: %s\n' "$_SCRATCH_DIR" >&2
    return 0
}

# Mint a detached checkout of <sha> into `_SCRATCH_CHECKOUT`. The trap is
# registered BEFORE `worktree add`, so a failed add still cleans up. Sets globals
# rather than printing a path: called inside `$(...)` the trap would belong to
# the subshell and fire the moment it returned.
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

cmd_list() {
    stage_table
}

cmd_check() {
    _parse '' "$@"
    [[ ${#STAGES[@]} -gt 0 ]] || _die 64 "check: no stage named"
    local store sha tree stage missing=0
    store="$(_store "$repo_root")" || _die 70 "cannot resolve the shared git dir of $repo_root"
    sha="$(_commit_of "$repo_root" "$REV")" || _die 70 "$REV names no commit in $repo_root"
    tree="$(_tree_of "$repo_root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    for stage in "${STAGES[@]}"; do
        if ! _marked "$store" "$tree" "$stage"; then
            printf '%s\n' "$stage"
            missing=1
        fi
    done
    exit "$missing"
}

cmd_subject() {
    _parse '' "$@"
    [[ ${#STAGES[@]} -eq 0 ]] || _die 64 "subject takes no stage"
    local sha tree
    sha="$(_commit_of "$repo_root" "$REV")" || _die 70 "$REV names no commit in $repo_root"
    tree="$(_tree_of "$repo_root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    printf 'repo=%s\ncommit=%s\ntree=%s\nwhere=%s\n' \
           "$repo_root" "$sha" "$tree" "$(_where "$repo_root" "$sha")"
}

# Short ids for a reader; the marker files keep the full ones.
_short() {
    git -C "$1" rev-parse --short "$2" 2>/dev/null || printf '%s' "$2"
}

# The last line of every run, green or not: what a reader who sees the tail
# first needs, and nothing the lines above did not already say in full.
_result() {
    local tree="$1" sha="$2" cached="$3" ran="$4" verdict="$5"
    printf '[gates] RESULT tree %s (%s): %s cached, %s ran, %s\n' \
           "$tree" "$sha" "$cached" "$ran" "$verdict" >&2
}

cmd_run() {
    _parse 1 "$@"
    [[ ${#STAGES[@]} -gt 0 ]] || _die 64 "run: no stage named"
    local store sha tree sha_s tree_s where checkout runner stage rc
    store="$(_store "$repo_root")" || _die 70 "cannot resolve the shared git dir of $repo_root"
    sha="$(_commit_of "$repo_root" "$REV")" || _die 70 "$REV names no commit in $repo_root"
    tree="$(_tree_of "$repo_root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    sha_s="$(_short "$repo_root" "$sha")"
    tree_s="$(_short "$repo_root" "$tree")"

    local -a cached=() todo=()
    for stage in "${STAGES[@]}"; do
        if [[ -z "$FRESH" ]] && _marked "$store" "$tree" "$stage"; then
            cached+=("$stage")
        else
            todo+=("$stage")
        fi
    done
    local ncached=${#cached[@]} nran=0

    # Nothing to run needs no checkout: a dirty desk with every stage earned
    # would otherwise mint and tear down a scratch worktree for nothing.
    if [[ ${#todo[@]} -eq 0 ]]; then
        printf '[gates] %s (tree %s)\n' "$sha_s" "$tree_s" >&2
        printf '[gates] cached (%s): %s\n' "$ncached" "${cached[*]}" >&2
        _result "$tree_s" "$sha_s" "$ncached" 0 green
        exit 0
    fi

    where="$(_where "$repo_root" "$sha")"
    if [[ "$where" == "in-place" ]]; then
        checkout="$repo_root"
        printf '[gates] %s (tree %s) in place: %s\n' "$sha_s" "$tree_s" "$checkout" >&2
    else
        _scratch_checkout "$repo_root" "$sha" \
            || _die 70 "could not check out $sha into a scratch worktree"
        checkout="$_SCRATCH_CHECKOUT"
        printf '[gates] %s (tree %s) scratch: %s\n' "$sha_s" "$tree_s" "$checkout" >&2
        # A PYTHON naming another checkout's interpreter would import that
        # checkout's source while claiming to judge this commit.
        if [[ -n "${PYTHON:-}" && "$PYTHON" != "$checkout"/* ]]; then
            printf '[gates] ignoring PYTHON=%s — it belongs to another checkout\n' "$PYTHON" >&2
            unset PYTHON
        fi
    fi
    if [[ $ncached -gt 0 ]]; then
        printf '[gates] cached (%s): %s\n' "$ncached" "${cached[*]}" >&2
    fi
    runner="$(_runner_in "$checkout")"
    [[ -f "$runner" ]] || _die 70 "no stage runner at $runner"

    if [[ "$where" == "scratch" ]]; then
        # A non-zero rc is REPORTED and the run goes on: a stage failing for
        # want of a dependency says so loudly; skipping here would say nothing.
        if ! (cd "$checkout" && bash "$runner" --prepare); then
            printf '[gates] the runner could not prepare %s (see above) — running anyway\n' \
                   "$checkout" >&2
        fi
    fi

    local addopts_exported=''
    for stage in "${todo[@]}"; do
        if [[ -z "$addopts_exported" ]] && _is_pytest_stage "$stage"; then
            _export_pytest_addopts "$checkout"
            addopts_exported=1
        fi
        nran=$((nran + 1))
        if (cd "$checkout" && bash "$runner" "$stage"); then
            rc=0
        else
            rc=$?
        fi
        if [[ "$rc" -ne 0 ]]; then
            _result "$tree_s" "$sha_s" "$ncached" "$nran" "FAILED $stage (rc=$rc)"
            exit "$rc"
        fi
        # A stage that changed tracked content ran the NEXT stages on something
        # other than the subject; the marker would then certify the wrong tree.
        if ! _clean "$checkout"; then
            printf '[gates] %s left the tree dirty — no marker for it:\n' "$stage" >&2
            git -C "$checkout" status --porcelain >&2
            _result "$tree_s" "$sha_s" "$ncached" "$nran" "FAILED $stage (rc=1): left the tree dirty"
            exit 1
        fi
        if ! _stamp "$store" "$tree" "$stage" "$sha" "$where"; then
            _result "$tree_s" "$sha_s" "$ncached" "$nran" \
                    "FAILED $stage (rc=70): green, but the marker could not be written"
            exit 70
        fi
        printf '[gates] %s: green, stamped\n' "$stage" >&2
    done
    _result "$tree_s" "$sha_s" "$ncached" "$nran" green
    exit 0
}

# ===========================================================================
# DISPATCH
# ===========================================================================

verb="${1:-all}"
case "$verb" in
    list) shift; cmd_list; exit 0 ;;
    check) shift; cmd_check "$@" ;;
    run) shift; cmd_run "$@" ;;
    subject) shift; cmd_subject "$@"; exit 0 ;;
esac
if [[ $# -gt 1 ]]; then
    echo "[gates] a stage runs bare: '${*:2}' after '$verb' is not accepted" >&2
    echo "  filter or parallelise through pytest itself, or PYTEST_ADDOPTS" >&2
    exit 64
fi
case "$verb" in
    # Asked BEFORE any stage runs, and its own process: `$PY` is resolved once
    # at the top, so an interpreter minted here is seen by the next invocation.
    --prepare) ci_prepare ;;
    all)
        # security is intentionally omitted — pip-audit is env-scoped (see NOTE).
        ci_tmp_sweep
        ci_lint
        ci_shellcheck
        ci_dependency_floor
        ci_python_pin
        ci_silent_fallback
        ci_bidi
        ci_test_isolation
        ci_e2e_catalog
        ci_env_reference
        ci_gate_table
        ci_adr_integrity
        ci_ticket_ids
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[gates] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        fn="ci_$(printf '%s' "$verb" | tr '-' '_')"
        if declare -F "$fn" >/dev/null 2>&1; then
            "$fn"
        else
            echo "[gates] unknown stage: $verb" >&2
            echo "  stages: $(known_stages | tr '\n' ' ')" >&2
            echo "  bundle: all (the local pre-push bundle, and the default)" >&2
            echo "  list | check <stage>... | run <stage>... | subject — the markers" >&2
            echo "  --prepare: mint a venv for this checkout (a precondition, never a check)" >&2
            exit 2
        fi
        ;;
esac
