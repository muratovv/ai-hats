#!/usr/bin/env bash
# HATS-922 — single source of truth for the CI test commands.
#
# Each CI job in .github/workflows/ci.yml calls one stage of this script, and a
# local pre-push run (`bash scripts/ci-local.sh`) runs them all. Because CI and
# the local gate share THIS file, their commands cannot silently drift — the
# root cause of "green locally, red in CI" (the coverage-job command was never
# run locally).
#
# Non-unit tools are invoked as `python -m <tool>` so the same line works in CI (deps
# pip-installed into `python`) and locally (venv python on PATH). Override the
# interpreter with PYTHON=/path/to/python.
#
# Usage:
#   scripts/ci-local.sh                   # the local bundle — the `all` branch names it
#   scripts/ci-local.sh tmp-sweep         # housekeeping only: reap dead test cruft from TMPDIR
#   scripts/ci-local.sh lint              # one stage (used by the matching CI job)
#   scripts/ci-local.sh coverage          # the stage that was the sole failing executor
#   scripts/ci-local.sh security          # CI-only stage; env-scoped (see NOTE below)
#   scripts/ci-local.sh no-such-stage     # exit 2, listing every stage there is
#
# A stage runs BARE — nothing after its name reaches pytest. A marker earned for
# `unit -k foo` would be a lie, so the primitive refuses the form and this
# dispatcher does not offer it; CI's parallelism rides PYTEST_ADDOPTS. This file
# knows stages and nothing else: which stages a GATE requires is
# `scripts/gates.sh`, and earning them is `scripts/ci-gate.sh`.
#
# NOTE: the `install-smoke` CI job is deliberately NOT a stage here — it runs
# install-launcher.sh which writes ~/.local/bin/ai-hats, an unwanted side effect
# on a dev box. It stays inline in ci.yml.
#
# NOTE: `security` (pip-audit) is a stage CI calls but is EXCLUDED from the local
# `all` bundle — pip-audit audits the active interpreter's WHOLE environment, so a
# polluted dev venv reports CVEs in packages ai-hats never declares (green in CI's
# clean install, red locally). CI is authoritative for the audit; run the stage
# explicitly against the project venv (`PYTHON=.venv/bin/python`) to reproduce CI.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python" ]]; then
    PY="$repo_root/.venv/bin/python"
elif [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python3" ]]; then
    PY="$repo_root/.venv/bin/python3"
else
    PY="${PYTHON:-python}"
fi

# HATS-1877: a stage that could NOT RUN is not a stage that refused. Python
# exits 1 on an uncaught ModuleNotFoundError exactly as a check exits 1 on a
# finding, so the code alone cannot tell the two apart — and the CI job that
# hosted `e2e-catalog` and `python-pin` without their imports reported each as
# its own subject for as long as neither had ever run.
#
# Only the fast checkers route through here. Their stderr is small enough to
# hold to the end of the stage; the pytest tiers stream for minutes, and a
# missing pytest is not a silent failure mode.
run_py() {
    local err rc=0 missing
    err="$(mktemp "${TMPDIR:-/tmp}/ci-local-stage.XXXXXX")" || err=''
    # No temp file, no detection — run plainly rather than lose the stage.
    if [[ -z "$err" ]]; then
        "$PY" "$@"
        return
    fi
    "$PY" "$@" 2>"$err" || rc=$?
    cat "$err" >&2
    missing="$(sed -n "s/^ModuleNotFoundError: No module named '\([^']*\)'.*/\1/p" "$err" | tail -1)"
    rm -f "$err"
    if [[ $rc -ne 0 && -n "$missing" ]]; then
        echo "[ci-local] BROKEN: this stage never ran — no module named '$missing'" >&2
        echo "  Nothing above is a finding. Install '$missing' for $PY, then re-run." >&2
        return 3
    fi
    return $rc
}

ci_tmp_sweep() {
    # HATS-1624: reap what killed runs leave in TMPDIR. Housekeeping, not a
    # check — it is in the `all` bundle and in NO gate composition, because a
    # gate names what must be green and this can only free space. Runs FIRST so
    # the heavy stages below get the space, and never fails the bundle: the
    # sweeper reports an unremovable dir on stderr and its own exit code says so.
    local sweep="$repo_root/scripts/clean-tmp-cruft.sh"
    [[ -x "$sweep" ]] || return 0
    echo "[ci-local] tmp-sweep (reap provably-dead test cruft)" >&2
    bash "$sweep" || echo "[ci-local] tmp-sweep left dirs behind (see above); continuing" >&2
}

ci_lint() {
    echo "[ci-local] lint (ruff check + format)" >&2
    run_py -m ruff check .
    # HATS-1372: the formatter check lived only in `make lint`, behind a failing
    # `ruff check` — so 20 unformatted files sat on master unseen for weeks.
    # HATS-1651: the same `.` as the line above. Two scopes could not stay equal
    # by convention, and ruff's own extend-exclude is the one that should decide.
    run_py -m ruff format --check .
}

ci_unit() {
    echo "[ci-local] unit (pytest -m 'not integration')" >&2
    env \
        -u VIRTUAL_ENV \
        -u VIRTUAL_ENV_PROMPT \
        -u PYTHONPATH \
        -u PYTHONHOME \
        -u PYTHONUSERBASE \
        uv run --isolated --no-project --python "$PY" --with-editable ".[dev]" \
        python -B -m pytest -m "not integration" -q
}

# HATS-1137: the integration tier OUTSIDE tests/e2e — the half `unit` excludes
# by marker and `merge-smoke` does not reach by path. Without it the done-gate
# would call itself green while skipping every real-subprocess test in tests/.
ci_integration() {
    echo "[ci-local] integration (pytest --ignore=tests/e2e -m integration)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e -m integration -q
}

ci_coverage() {
    echo "[ci-local] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e/ \
        --cov=ai_hats \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=78 \
        -q
}

ci_security() {
    # HATS-1591: bandit dropped — ruff's `S` family covers its inventory and
    # already runs on every road. What is left here is the network half.
    echo "[ci-local] security (pip-audit)" >&2
    run_py -m pip_audit
}

ci_merge_smoke() {
    echo "[ci-local] merge-smoke (curated e2e subset)" >&2
    "$PY" -B -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q
}

# Offline and instant, so unlike version-skew it belongs in the `all` bundle.
ci_dependency_floor() {
    echo "[ci-local] dependency-floor (pins vs workspace versions)" >&2
    run_py scripts/check_dependency_floor.py
}

# Offline and instant, like the others. HATS-1599: a ratchet, so it is green
# only while the tree patches its own units no more than the recorded baseline.
ci_test_isolation() {
    echo "[ci-local] test-isolation (patching of code under test vs the baseline)" >&2
    run_py scripts/check_test_isolation.py
}

# Offline and instant. HATS-1591: the one check bandit held that ruff's `S`
# family does not, kept after bandit itself was dropped.
ci_bidi() {
    echo "[ci-local] bidi (bidirectional controls, invisible in review)" >&2
    run_py scripts/check_bidi.py
}

# Offline and instant, like dependency-floor — so it belongs in `all` too.
ci_python_pin() {
    echo "[ci-local] python-pin (every copy of the pin agrees; CI runs it)" >&2
    run_py scripts/check_python_pin.py
}

# Offline and instant, like dependency-floor — so it belongs in `all` too.
ci_silent_fallback() {
    echo "[ci-local] silent-fallback (broad handlers nothing can escape from)" >&2
    run_py scripts/check_silent_fallback.py
}

# Offline and instant, like the two above. HATS-1498: the flow catalog is
# rendered from the tests' own docstrings, so it goes stale the moment one is
# edited without regenerating.
ci_e2e_catalog() {
    echo "[ci-local] e2e-catalog (tests/e2e/CATALOG.md vs the flow blocks)" >&2
    run_py scripts/gen_e2e_catalog.py --check
}

# Offline and instant, like the three above. HATS-1646: prose carries no assert,
# so a citation into an ADR rots green — two such defects lived for months.
ci_adr_integrity() {
    echo "[ci-local] adr-integrity (ADR citations resolve; a number names one file)" >&2
    run_py scripts/check_adr_integrity.py
}

# Offline and instant, like the four above. HATS-1825: `adr-integrity` proved a
# gate on prose is buildable and was aimed at one rigid citation form; this is the
# same gate aimed at the library's own references, where 21 had already rotted.
ci_prose_refs() {
    echo "[ci-local] prose-refs (paths, library prefixes, sections and symbols in library prose)" >&2
    run_py scripts/check_prose_refs.py
}

# Offline and instant, like the five above. Sibling of `prose-refs` aimed at what
# prose must NOT carry rather than what it must resolve: the library installs
# into other projects, where this repo's tracker ids are dead links. It reports
# the ids it still finds in docs/adr and CHANGELOG, so a clean run proves the
# pattern is alive rather than merely silent.
ci_ticket_ids() {
    echo "[ci-local] ticket-ids (no tracker id in shipped library prose)" >&2
    run_py scripts/check_no_ticket_ids.py
}

# Offline and instant, like the six above. HATS-1872: sibling of `e2e-catalog` —
# the env reference page is rendered from the declarations the code itself reads,
# so it goes stale the moment a default is edited without regenerating. A stale
# NUMBER is worse than no page: prose invites a check, a number invites trust.
ci_env_reference() {
    echo "[ci-local] env-reference (docs/reference-env.md vs the env declarations)" >&2
    run_py scripts/gen_env_reference.py --check
}

# The full maintainer tier (the slow one). Excluded from `all`; this is the selection
# the master pre-push gate runs, kept here so `make e2e` cannot mean something
# narrower than the gate that guards the push (HATS-1372).
ci_e2e() {
    echo "[ci-local] e2e (integration + smoke, quarantine and live agy excluded)" >&2
    "$PY" -B -m pytest -m "(integration or smoke) and not quarantine and not live_agy" tests/e2e/ tests/smoke/ -q
}

# Make THIS checkout runnable, so `$PY` above resolves to an interpreter that
# imports this tree and not another one. NOT a stage and in no gate composition:
# it asserts nothing and can only be a precondition (HATS-1664).
#
# Who asks: the gate primitive, before running a gate inside a scratch checkout
# of a merge commit — that checkout is minted by `git worktree add` and has no
# `.venv` at all, so without this every real-subprocess test would exercise the
# MAIN checkout's installed code while claiming to judge the commit. The hook it
# delegates to is the same one every task worktree gets (HATS-1291); an already
# usable `.venv` makes it a no-op.
ci_prepare() {
    echo "[ci-local] prepare (a venv for this checkout, if it needs one)" >&2
    # From the TREE, not from an installed library: an unprepared checkout has no
    # interpreter that could import one, and this repository carries the hook's
    # source anyway — so the version that runs is the one belonging to the
    # content under judgement.
    local hook="$repo_root/packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/worktree-venv/hooks/provision-venv.sh"
    if [[ ! -f "$hook" ]]; then
        echo "[ci-local] no provision-venv hook at $hook — nothing to prepare" >&2
        return 1
    fi
    AI_HATS_WORKTREE_PATH="$repo_root" bash "$hook"
}

# NOTE: excluded from the local `all` bundle — it queries PyPI, so an offline
# dev box would fail a legitimate push. CI is authoritative; run explicitly
# (optionally SKEW_BASE=<sha>) to reproduce.
ci_version_skew() {
    echo "[ci-local] version-skew (workspace pkgs ahead of PyPI)" >&2
    run_py scripts/check_pkg_version_skew.py "${SKEW_BASE:-origin/master}"
}

# Offline and instant, like the seven above. HATS-1877: the shell this repo
# SHIPS is the half no python linter ever saw — `git_hooks/**` and the skills'
# `hooks/**` run in a consuming project, where a portability bug is a silent
# skip rather than a crash. Severity is capped at `warning` on purpose: `info`
# and below is 41 findings, twenty of them SC1091 on dynamically sourced libs.
ci_shellcheck() {
    echo "[ci-local] shellcheck (tracked *.sh, severity >= warning)" >&2
    if ! command -v shellcheck >/dev/null 2>&1; then
        echo "[shellcheck] SKIPPED: not installed — no shell script was checked." >&2
        return 0
    fi
    local count
    count="$(git ls-files -- '*.sh' | wc -l | tr -d ' ')"
    # xargs with no input still runs the utility once, and shellcheck with no
    # file exits on its usage — a red stage for an empty set.
    if [[ "$count" -eq 0 ]]; then
        echo "[shellcheck] no tracked *.sh — nothing was checked." >&2
        return 0
    fi
    git ls-files -z -- '*.sh' | xargs -0 shellcheck -S warning
    echo "[shellcheck] ok: $count file(s) clean" >&2
}

# HATS-1877: offline given a warm uv cache, and ~3s for all five packages, so it
# sits on `->merge` — the last edge before a version can be published. It is the
# only check here that judges the ARTEFACT rather than the tree.
ci_wheel_contents() {
    echo "[ci-local] wheel-contents (tracked src files vs the sdist-chained wheel)" >&2
    run_py scripts/check_wheel_contents.py
}

# HATS-1877: NETWORK — like `version-skew`, so it is excluded from the local
# `all` bundle and from CI (where master's own verdict is circular). It sits on
# `->done` alone: the month of red that hid seven of v0.15.0's nine defects was
# a signal nobody read, and the close is the moment a human is looking.
ci_master_ci() {
    echo "[ci-local] master-ci (master's last CI verdict)" >&2
    run_py scripts/check_master_ci.py
}

# The stage set IS the set of `ci_*` functions defined above: the dispatch and
# the usage line below both read it, so a stage can no longer be reachable and
# unlisted (`tmp-sweep` was, HATS-1716) or listed and unreachable. The other
# side of the convention: a helper that is not a stage does not take the `ci_`
# prefix — `run_py` is one.
known_stages() {
    declare -F | sed -n 's/^declare -f ci_//p' | tr '_' '-' | sort
}

stage="${1:-all}"
if [[ $# -gt 1 ]]; then
    echo "[ci-local] a stage runs bare: '${*:2}' after '$stage' is not accepted" >&2
    echo "  filter or parallelise through pytest itself, or PYTEST_ADDOPTS" >&2
    exit 64
fi
case "$stage" in
    # Not a stage: it is asked BEFORE any stage runs, and it must be its own
    # process — `$PY` is resolved once at the top of this script, so an
    # interpreter minted here is only seen by the next invocation (HATS-1664).
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
        ci_adr_integrity
    ci_ticket_ids
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[ci-local] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        fn="ci_$(printf '%s' "$stage" | tr '-' '_')"
        if declare -F "$fn" >/dev/null 2>&1; then
            "$fn"
        elif composition="$(bash "$repo_root/scripts/gates.sh" stages "$stage" 2>/dev/null)"; then
            # A gate is not a stage: it is a NAME for a set of them, and running
            # it is the primitive's job (it owns the markers).
            echo "[ci-local] '$stage' is a gate, not a stage — it names: $composition" >&2
            echo "  its composition:  scripts/gates.sh stages $stage" >&2
            echo "  run it (stamps each green stage):  scripts/gates.sh run $stage" >&2
            exit 2
        else
            echo "[ci-local] unknown stage: $stage" >&2
            echo "  stages: $(known_stages | tr '\n' ' ')" >&2
            echo "  bundle: all (the local pre-push bundle, and the default)" >&2
            echo "  gates (a NAME for a set of stages): scripts/gates.sh list" >&2
            echo "  --prepare: mint a venv for this checkout (a precondition, never a check)" >&2
            exit 2
        fi
        ;;
esac
