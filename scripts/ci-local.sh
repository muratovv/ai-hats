#!/usr/bin/env bash
# HATS-922 — single source of truth for the CI test commands.
#
# Each CI job in .github/workflows/ci.yml calls one stage of this script, and a
# local pre-push run (`bash scripts/ci-local.sh`) runs them all. Because CI and
# the local gate share THIS file, their commands cannot silently drift — the
# root cause of "green locally, red in CI" (the coverage-job command was never
# run locally).
#
# Tools are invoked as `python -m <tool>` so the same line works in CI (deps
# pip-installed into `python`) and locally (venv python on PATH). Override the
# interpreter with PYTHON=/path/to/python.
#
# Usage:
#   scripts/ci-local.sh            # local bundle: lint dependency-floor unit coverage merge-smoke
#   scripts/ci-local.sh lint       # one stage (used by the matching CI job)
#   scripts/ci-local.sh coverage   # the stage that was the sole failing executor
#   scripts/ci-local.sh security   # CI-only stage; env-scoped (see NOTE below)
#   scripts/ci-local.sh done-gate  # what edge:review--done demands (HATS-1137)
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

ci_lint() {
    echo "[ci-local] lint (ruff check + format)" >&2
    "$PY" -m ruff check .
    # HATS-1372: the formatter check lived only in `make lint`, behind a failing
    # `ruff check` — so 20 unformatted files sat on master unseen for weeks.
    "$PY" -m ruff format --check src/ tests/
}

ci_unit() {
    echo "[ci-local] unit (pytest -m 'not integration')" >&2
    "$PY" -B -m pytest -m "not integration" -q ${@+"$@"}
}

# HATS-1137: the integration tier OUTSIDE tests/e2e — the half `unit` excludes
# by marker and `merge-smoke` does not reach by path. Without it the done-gate
# would call itself green while skipping every real-subprocess test in tests/.
ci_integration() {
    echo "[ci-local] integration (pytest --ignore=tests/e2e -m integration)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e -m integration -q ${@+"$@"}
}

ci_coverage() {
    echo "[ci-local] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e/ \
        --cov=ai_hats \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=78 \
        -q ${@+"$@"}
}

ci_security() {
    echo "[ci-local] security (bandit + pip-audit)" >&2
    "$PY" -m bandit -r src/ -ll -q
    "$PY" -m pip_audit
}

ci_merge_smoke() {
    echo "[ci-local] merge-smoke (curated e2e subset)" >&2
    "$PY" -B -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q ${@+"$@"}
}

# Offline and instant, so unlike version-skew it belongs in the `all` bundle.
ci_dependency_floor() {
    echo "[ci-local] dependency-floor (pins vs workspace versions)" >&2
    "$PY" scripts/check_dependency_floor.py
}

# Offline and instant, like the others. HATS-1599: a ratchet, so it is green
# only while the tree patches its own units no more than the recorded baseline.
ci_test_isolation() {
    echo "[ci-local] test-isolation (patching of code under test vs the baseline)" >&2
    "$PY" scripts/check_test_isolation.py
}

# Offline and instant, like dependency-floor — so it belongs in `all` too.
ci_silent_fallback() {
    echo "[ci-local] silent-fallback (broad handlers nothing can escape from)" >&2
    "$PY" scripts/check_silent_fallback.py
}

# Offline and instant, like the two above. HATS-1498: the flow catalog is
# rendered from the tests' own docstrings, so it goes stale the moment one is
# edited without regenerating.
ci_e2e_catalog() {
    echo "[ci-local] e2e-catalog (tests/e2e/CATALOG.md vs the flow blocks)" >&2
    "$PY" scripts/gen_e2e_catalog.py --check
}

# The full maintainer tier (~25 min). Excluded from `all`; this is the selection
# the master pre-push gate runs, kept here so `make e2e` cannot mean something
# narrower than the gate that guards the push (HATS-1372).
ci_e2e() {
    echo "[ci-local] e2e (integration + smoke, quarantine excluded)" >&2
    "$PY" -B -m pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ -q ${@+"$@"}
}

# HATS-1137/HATS-1604 — what each gate is made of, and the ONE place it is
# configured. A gate script asks with `<gate> --stages` and runs the stages
# through the shared primitive, so "green enough to be done" is an edit HERE and
# the library never restates it (ADR-0023 D7).
#
# Neither gate joins `all`: `all` is the pre-push bundle and already runs
# `coverage`, which collects the same non-e2e integration tests unfiltered.
gate_composition() {
    case "$1" in
        done-gate) echo "e2e-catalog lint unit integration merge-smoke" ;;
        push-gate) echo "lint unit e2e-catalog e2e" ;;
        *) return 1 ;;
    esac
}

# NOTE: excluded from the local `all` bundle — it queries PyPI, so an offline
# dev box would fail a legitimate push. CI is authoritative; run explicitly
# (optionally SKEW_BASE=<sha>) to reproduce.
ci_version_skew() {
    echo "[ci-local] version-skew (workspace pkgs ahead of PyPI)" >&2
    "$PY" scripts/check_pkg_version_skew.py "${SKEW_BASE:-origin/master}"
}

stage="${1:-all}"
shift 2>/dev/null || true   # remaining argv is passed through to the pytest stages
case "$stage" in
    lint) ci_lint ${@+"$@"} ;;
    unit) ci_unit ${@+"$@"} ;;
    integration) ci_integration ${@+"$@"} ;;
    coverage) ci_coverage ${@+"$@"} ;;
    # A gate is not a stage: it is a NAME for a set of them, and running it is
    # the primitive's job (it owns the marker). `--stages` comes FIRST so a
    # dispatcher that does not know the flag refuses instantly instead of
    # mistaking it for an argument to a gate it does know (HATS-1604).
    --stages)
        gate_composition "${1:-}" || {
            echo "[ci-local] no such gate: ${1:-<none>} (gates: done-gate | push-gate)" >&2
            exit 2
        }
        ;;
    done-gate|push-gate)
        echo "[ci-local] '$stage' is a gate, not a stage — it names: $(gate_composition "$stage")" >&2
        echo "  its composition:  scripts/ci-local.sh --stages $stage" >&2
        echo "  run it (marks the tree on green):  make done-gate | scripts/run-e2e-gate.sh" >&2
        exit 2
        ;;
    security) ci_security ${@+"$@"} ;;
    merge-smoke) ci_merge_smoke ${@+"$@"} ;;
    dependency-floor) ci_dependency_floor ;;
    silent-fallback) ci_silent_fallback ;;
    test-isolation) ci_test_isolation ;;
    e2e-catalog) ci_e2e_catalog ;;
    e2e) ci_e2e ${@+"$@"} ;;
    version-skew) ci_version_skew ${@+"$@"} ;;
    all)
        # security is intentionally omitted — pip-audit is env-scoped (see NOTE).
        ci_lint
        ci_dependency_floor
        ci_silent_fallback
        ci_test_isolation
        ci_e2e_catalog
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[ci-local] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        echo "[ci-local] unknown stage: $stage" >&2
        echo "  stages: lint | unit | integration | coverage | security | merge-smoke | e2e | e2e-catalog | dependency-floor | silent-fallback | test-isolation | version-skew | all" >&2
        echo "  gates (--stages prints their composition): done-gate | push-gate" >&2
        exit 2
        ;;
esac
