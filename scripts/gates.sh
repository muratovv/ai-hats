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
#   gates.sh touched [--rev C] [--base B]  # zone stages this change demands
#   gates.sh zones                      # prefix | marker | stage, one per zone
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
consumer-refs    | no library component names a component that composes it
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
e2e-default      | the half of the tier no zone claims — an unexpected regression
e2e-rack         | the rack zone of the tier: what a change under packages/ai-hats-rack/ is expected to break
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

# Which way a reference points. A role saying why it takes a trait points down;
# a trait naming the role that takes it points up, and rots the moment that role
# drops it. The composition graph is what makes the direction checkable.
ci_consumer_refs() {
    echo "[gates] consumer-refs (no component names a component that composes it)" >&2
    run_py scripts/check_consumer_refs.py
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

# ZONES: which change makes which part of the tier expected to break.
#
# A zone is a marker on the tests and the path prefix that owns them. `touched`
# turns a diff into the zone stages it demands, so a card editing a zone runs it
# at `->merge`, where a refusal points at the agent's own branch. What no zone
# claims is `e2e-default`, and it stands at `->done`, where a supervisor is
# present for the breakage two green branches made together (ADR-0023 D3).
#
# A zone marker MOVES a test between gates; it never removes it from the tier.
# `push-gate` and CI run the whole of it either way, so a row missing here costs
# today's level, never less.
zone_table() {
    cat <<'TABLE'
packages/ai-hats-rack/ | rack
TABLE
}

# A zone's stage name, spelled in ONE place: the verb that demands it and the
# renderer that documents it must agree, and a convention spread over two files
# is a convention that drifts.
_zone_stage() {
    printf 'e2e-%s' "$1"
}

# Every zone as one pytest expression: `rack`, then `rack or wt`, and so on.
# Empty while no zone exists, which is what makes `e2e-default` the whole tier
# until the first row lands.
_zone_expr() {
    zone_table | awk -F'|' '
        { gsub(/[[:space:]]/, "", $2); if ($2 != "") printf "%s%s", (n++ ? " or " : ""), $2 }
        END { printf "\n" }'
}

# The tier's selection lives HERE and nowhere else: `e2e` is the whole of it and
# the zone stages are it narrowed. Hand-kept copies of this expression is the
# defect that already cost this repo once — the tier's selection lived in two
# copies with no test holding them equal — so `tests/test_e2e_zone_partition.py`
# holds the parts equal to the whole.
E2E_SELECT='(integration or smoke) and not quarantine and not live_agy'
E2E_PATHS='tests/e2e/ tests/smoke/'

# The full maintainer tier (the slow one). Excluded from `all`; what CI runs and
# what the zone stages partition, kept here so `make e2e` cannot mean something
# narrower.
ci_e2e() {
    echo "[gates] e2e (integration + smoke, quarantine and live agy excluded)" >&2
    # shellcheck disable=SC2086 # E2E_PATHS is two paths and must split
    "$PY" -B -m pytest -m "$E2E_SELECT" $E2E_PATHS -q
}

# The half no zone claims — an unexpected regression, judged where a supervisor
# is present. It shrinks as zones are declared; an empty zone table makes it the
# whole tier.
ci_e2e_default() {
    echo "[gates] e2e-default (the tier outside every zone)" >&2
    local zones select
    zones="$(_zone_expr)"
    if [[ -n "$zones" ]]; then
        select="$E2E_SELECT and not ($zones)"
    else
        select="$E2E_SELECT"
    fi
    # shellcheck disable=SC2086
    "$PY" -B -m pytest -m "$select" $E2E_PATHS -q
}

# One function per zone row, by hand: the stage set is `declare -F`, so a
# generated name would be a stage nothing can list.
ci_e2e_rack() {
    echo "[gates] e2e-rack (the rack zone of the tier)" >&2
    # shellcheck disable=SC2086
    "$PY" -B -m pytest -m "$E2E_SELECT and rack" $E2E_PATHS -q
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
PYTEST_STAGES='unit integration coverage merge-smoke e2e e2e-default e2e-rack'

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
    # An empty tree dir younger than an hour is a parallel run between its
    # mkdir and its mktemp; reaping it turned that run's green into exit 70.
    find "$store" -mindepth 1 -type d -empty -mmin +60 -delete 2>/dev/null || true
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

# Bare stage invocations stay serial; a run uses the cores. What was found
# goes into ADDOPTS_NOTE for the run's block rather than straight to stderr.
ADDOPTS_NOTE=''
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
        ADDOPTS_NOTE="pytest-xdist detected — -n$n --dist=loadgroup (cores=$cores, cap=$ceiling)"
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

# The branch a card lands on. A shell copy of ai-hats-wt's
# CANONICAL_BASE_BRANCHES, held equal to it by `tests/test_zone_touched.py`:
# a drifted copy names the wrong base, and a wrong base is a wrong diff.
CANONICAL_BASE_BRANCHES='master main'

_base_branch() {
    local name
    for name in $CANONICAL_BASE_BRANCHES; do
        if git -C "$1" show-ref --verify --quiet "refs/heads/$name"; then
            printf '%s' "$name"
            return 0
        fi
    done
    return 1
}

# Which zone stages a change demands — the diff turned into stage names, one per
# line. NEVER SILENT ABOUT NOT KNOWING: a base it cannot name exits non-zero,
# because "nothing changed" and "I could not tell" are the same empty output,
# and a gate reading the second as the first passes what it never examined.
#
# The base has two roads, the same two the subject has (ADR-0023 D4): a merge
# commit is judged against its first parent, so the diff is exactly what the card
# contributed; anything else against where it left the base branch.
cmd_touched() {
    local base='' rev='HEAD'
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rev)
                [[ -n "${2:-}" ]] || _die 64 "--rev names no commit"
                rev="$2"
                shift 2
                ;;
            --base)
                [[ -n "${2:-}" ]] || _die 64 "--base names no commit"
                base="$2"
                shift 2
                ;;
            *) _die 64 "touched: unexpected argument '$1'" ;;
        esac
    done

    local sha
    sha="$(_commit_of "$repo_root" "$rev")" || _die 70 "$rev names no commit in $repo_root"
    if [[ -z "$base" ]]; then
        if git -C "$repo_root" rev-parse --verify --quiet "$sha^2" >/dev/null 2>&1; then
            base="$sha^1"
        else
            local branch
            branch="$(_base_branch "$repo_root")" \
                || _die 70 "no base branch here (looked for: $CANONICAL_BASE_BRANCHES) — cannot tell what changed"
            base="$(git -C "$repo_root" merge-base "$sha" "$branch" 2>/dev/null)" \
                || _die 70 "no merge base between $rev and $branch — cannot tell what changed"
        fi
    fi

    local changed
    changed="$(git -C "$repo_root" diff --name-only "$base" "$sha" 2>/dev/null)" \
        || _die 70 "cannot diff $base..$sha — cannot tell what changed"

    local prefix zone path hit
    while IFS='|' read -r prefix zone; do
        prefix="${prefix//[[:space:]]/}"
        zone="${zone//[[:space:]]/}"
        [[ -n "$prefix" && -n "$zone" ]] || continue
        # A prefix owning nothing is a zone that can never be demanded, and it is
        # NOT checked here: this table belongs to the repository that ships it,
        # while `touched` runs against whatever tree is being judged — a scratch
        # project carrying this script has none of these paths, and refusing
        # there broke the gate for every test that plants it.
        # `tests/test_zone_touched.py` holds the rows resolvable in THIS repo.
        hit=''
        while IFS= read -r path; do
            [[ -n "$path" ]] || continue
            case "$path" in "$prefix"*) hit=1; break ;; esac
        done <<< "$changed"
        [[ -n "$hit" ]] && printf '%s\n' "$(_zone_stage "$zone")"
    done < <(zone_table)
    return 0
}

# The zone table with each row's stage name resolved: `prefix | marker | stage`.
# What `gen_gate_table.py` reads so ADR-0023 can say which stages are demanded by
# a diff rather than by a gate's declaration — a row it had to guess at by the
# shape of a stage name would be a guess the `gate-table` stage then enforced.
cmd_zones() {
    [[ $# -eq 0 ]] || _die 64 "zones takes no argument"
    local prefix zone
    while IFS='|' read -r prefix zone; do
        prefix="${prefix//[[:space:]]/}"
        zone="${zone//[[:space:]]/}"
        [[ -n "$prefix" && -n "$zone" ]] || continue
        printf '%s | %s | %s\n' "$prefix" "$zone" "$(_zone_stage "$zone")"
    done < <(zone_table)
}

# Short ids for a reader; the marker files keep the full ones.
_short() {
    git -C "$1" rev-parse --short "$2" 2>/dev/null || printf '%s' "$2"
}

# The first line of every run's block, green or not.
_result() {
    local tree="$1" sha="$2" cached="$3" ran="$4" verdict="$5"
    printf '[gates] RESULT tree %s (%s): %s cached, %s ran, %s\n' \
           "$tree" "$sha" "$cached" "$ran" "$verdict" >&2
}

_in() {
    local dir="$1"
    shift
    (cd "$dir" && "$@")
}

# A stage's both streams go to its log. A terminal sees them live as well;
# a captured stream (an agent's) sees only the block at the end.
LIVE=''
_capture() {
    local log="$1"
    shift
    if [[ -n "$LIVE" ]]; then
        "$@" 2>&1 | tee "$log" >&2
    else
        "$@" > "$log" 2>&1
    fi
}

# A stage's last non-empty line, its own `[stage]` tag dropped: the block
# already names the stage in front of it.
_last_line() {
    local log="$1" stage="${2:-}" line
    line="$(grep -v '^[[:space:]]*$' "$log" 2>/dev/null | tail -1 || true)"
    printf '%s' "${line#"[$stage] "}"
}

# The printed copy of a log, without pytest's progress dots; the file keeps them.
_print_log() {
    grep -v -E '^[.sFxXE]+( +\[ *[0-9]+%\])?$' "$1" >&2 || true
}

# Past this many, a re-run line is not a command any more; the transcript path
# in the block is the better answer.
RERUN_MAX_IDS=20

# The node ids a red pytest stage named, in pytest's own order. `FAILED <id> -
# <reason>` and `ERROR <id>` are its short summary; the reason is prose, not
# argv, so it is cut — an id carrying its own ` - ` loses its tail, which is why
# the ids are printed back to a reader who can see the log right below.
_failed_ids() {
    sed -n -E 's/^(FAILED|ERROR) (.*)$/\2/p' "$1" 2>/dev/null \
        | sed -E 's/ - .*$//' \
        | awk 'NF && !seen[$0]++'
}

# The one command that re-runs just what failed. What pytest's summary cannot
# name is the interpreter this checkout answers for — the decision a hand-built
# invocation gets wrong, and the one the worktree guard refuses. No flags: the
# gate's own PYTEST_ADDOPTS (`--tb=line`, xdist) is what a reader wants OFF.
# Prints nothing rather than something unrunnable.
_rerun_cmd() {
    local log="$1" ids count id out=''
    [[ -f "$log" ]] || return 1
    ids="$(_failed_ids "$log")"
    [[ -n "$ids" ]] || return 1
    # A quote in a node id cannot be spelled safely in one line, and no amount
    # of escaping makes a half-quoted command worth pasting.
    case "$ids" in *\'*) return 1 ;; esac
    count="$(printf '%s\n' "$ids" | wc -l | tr -d ' ')"
    [[ "$count" -le "$RERUN_MAX_IDS" ]] || return 1
    while IFS= read -r id; do
        case "$id" in
            *[!A-Za-z0-9_./:@=+~-]*) id="'$id'" ;;
        esac
        out="$out $id"
    done <<< "$ids"
    printf '%s -m pytest%s' "$PY" "$out"
}

# A run prints ONE block, in order of importance: the verdict, what to do, what
# the red stage said, one line per green stage, the cached ones, the subject,
# and where the full transcripts are. The reader is usually an agent looking
# at a captured stream, and the first lines are what it acts on.
cmd_run() {
    _parse 1 "$@"
    [[ ${#STAGES[@]} -gt 0 ]] || _die 64 "run: no stage named"
    local store sha tree sha_s tree_s where checkout runner stage rc=0
    store="$(_store "$repo_root")" || _die 70 "cannot resolve the shared git dir of $repo_root"
    sha="$(_commit_of "$repo_root" "$REV")" || _die 70 "$REV names no commit in $repo_root"
    tree="$(_tree_of "$repo_root" "$sha")" || _die 70 "cannot resolve the tree of $sha"
    sha_s="$(_short "$repo_root" "$sha")"
    tree_s="$(_short "$repo_root" "$tree")"

    local -a cached=() todo=() ran_green=() notes=()
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
        _result "$tree_s" "$sha_s" "$ncached" 0 green
        printf '[gates] cached (%s): %s\n' "$ncached" "${cached[*]}" >&2
        printf '[gates] %s (tree %s)\n' "$sha_s" "$tree_s" >&2
        exit 0
    fi

    if [[ -t 2 ]]; then
        LIVE=1
    fi
    local run_dir
    local tmp="${TMPDIR:-/tmp}"
    run_dir="$(mktemp -d "${tmp%/}/gates-run.XXXXXX")" || _die 70 "cannot make a run dir under $tmp"

    local subject
    where="$(_where "$repo_root" "$sha")"
    if [[ "$where" == "in-place" ]]; then
        checkout="$repo_root"
        subject="$sha_s (tree $tree_s) in place: $checkout"
    else
        _scratch_checkout "$repo_root" "$sha" \
            || _die 70 "could not check out $sha into a scratch worktree"
        checkout="$_SCRATCH_CHECKOUT"
        subject="$sha_s (tree $tree_s) scratch: $checkout"
        # A PYTHON naming another checkout's interpreter would import that
        # checkout's source while claiming to judge this commit.
        if [[ -n "${PYTHON:-}" && "$PYTHON" != "$checkout"/* ]]; then
            notes+=("ignoring PYTHON=$PYTHON — it belongs to another checkout")
            unset PYTHON
        fi
    fi
    runner="$(_runner_in "$checkout")"
    [[ -f "$runner" ]] || _die 70 "no stage runner at $runner"

    if [[ "$where" == "scratch" ]]; then
        # A non-zero rc is REPORTED and the run goes on: a stage failing for
        # want of a dependency says so loudly; skipping here would say nothing.
        if _capture "$run_dir/prepare.log" _in "$checkout" bash "$runner" --prepare; then
            notes+=("prepare: $(_last_line "$run_dir/prepare.log" worktree-venv)")
        else
            notes+=("the runner could not prepare $checkout — ran anyway; see $run_dir/prepare.log")
        fi
    fi

    # A dirty desk is judged in a scratch checkout of the subject, so the WHOLE
    # run answers about committed content — the tier that clones to build its
    # venv (tests/e2e) is the loudest case of that, not the only one. It belongs
    # under the verdict either way: a red one gets believed against a fix that
    # was never there, a green one vouches for a fix that never ran. A terminal
    # also gets it up front, where minutes of tier still separate the two; a
    # captured stream does not, because there the two lines would be adjacent.
    local desk_note='' named
    if ! _clean "$repo_root"; then
        if [[ "$REV" == "HEAD" ]]; then
            named="HEAD ($sha_s)"
        else
            named="$sha_s"
        fi
        desk_note="the desk is dirty — this run judges $named in a scratch checkout;"
        desk_note="$desk_note uncommitted changes are invisible to it"
        if [[ -n "$LIVE" ]]; then
            printf '[gates] %s\n' "$desk_note" >&2
        fi
    fi

    local verdict='' failed='' dirty=''
    for stage in "${todo[@]}"; do
        if [[ -z "$ADDOPTS_NOTE" ]] && _is_pytest_stage "$stage"; then
            _export_pytest_addopts "$checkout"
        fi
        nran=$((nran + 1))
        if _capture "$run_dir/$stage.log" _in "$checkout" bash "$runner" "$stage"; then
            rc=0
        else
            rc=$?
        fi
        if [[ "$rc" -ne 0 ]]; then
            verdict="FAILED $stage (rc=$rc)"
            failed="$stage"
            break
        fi
        # A stage that changed tracked content ran the NEXT stages on something
        # other than the subject; the marker would then certify the wrong tree.
        if ! _clean "$checkout"; then
            dirty="$(git -C "$checkout" status --porcelain)"
            verdict="FAILED $stage (rc=1): left the tree dirty"
            failed="$stage"
            rc=1
            break
        fi
        if ! _stamp "$store" "$tree" "$stage" "$sha" "$where"; then
            verdict="FAILED $stage (rc=70): green, but the marker could not be written"
            failed="$stage"
            rc=70
            break
        fi
        ran_green+=("$stage")
    done

    _result "$tree_s" "$sha_s" "$ncached" "$nran" "${verdict:-green}"
    if [[ -n "$desk_note" ]]; then
        printf '[gates] %s\n' "$desk_note" >&2
    fi
    if [[ -n "$failed" ]]; then
        # The gate hands the command down; bare, the primitive can only say "again".
        local then_what="${GATES_RESUME_CMD:+: $GATES_RESUME_CMD}"
        printf '[gates] fix %s, then%s\n' "$failed" "${then_what:- run this again}" >&2
        local rerun
        if _is_pytest_stage "$failed" && rerun="$(_rerun_cmd "$run_dir/$failed.log")"; then
            printf '[gates] re-run just these:\n    %s\n' "$rerun" >&2
        fi
        if [[ -n "$dirty" ]]; then
            printf '[gates] %s left the tree dirty:\n%s\n' "$failed" "$dirty" >&2
        fi
        if [[ -z "$LIVE" ]]; then
            printf '[gates] %s said:\n' "$failed" >&2
            _print_log "$run_dir/$failed.log"
        fi
    fi
    local said
    for stage in ${ran_green[@]+"${ran_green[@]}"}; do
        said="$(_last_line "$run_dir/$stage.log" "$stage")"
        printf '[gates] %s: %s\n' "$stage" "${said:-green}" >&2
    done
    if [[ $ncached -gt 0 ]]; then
        printf '[gates] cached (%s): %s\n' "$ncached" "${cached[*]}" >&2
    fi
    printf '[gates] %s\n' "$subject" >&2
    if [[ -n "$ADDOPTS_NOTE" ]]; then
        printf '[gates] %s\n' "$ADDOPTS_NOTE" >&2
    fi
    local note
    for note in ${notes[@]+"${notes[@]}"}; do
        printf '[gates] %s\n' "$note" >&2
    done
    printf '[gates] transcript: %s\n' "$run_dir" >&2
    exit "$rc"
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
    touched) shift; cmd_touched "$@"; exit 0 ;;
    zones) shift; cmd_zones "$@"; exit 0 ;;
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
        ci_consumer_refs
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
            echo "  touched: the zone stages this change demands (a diff, not a marker)" >&2
            echo "  zones: the zone table — prefix | marker | stage" >&2
            echo "  --prepare: mint a venv for this checkout (a precondition, never a check)" >&2
            exit 2
        fi
        ;;
esac
