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

PY="${PYTHON:-python}"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

ci_lint() {
    echo "[ci-local] lint (ruff check + format)" >&2
    "$PY" -m ruff check .
    # HATS-1372: the formatter check lived only in `make lint`, behind a failing
    # `ruff check` — so 20 unformatted files sat on master unseen for weeks.
    "$PY" -m ruff format --check src/ tests/
}

ci_unit() {
    echo "[ci-local] unit (pytest -m 'not integration')" >&2
    "$PY" -m pytest -m "not integration" -q ${@+"$@"}
}

# HATS-1137: the integration tier OUTSIDE tests/e2e — the half `unit` excludes
# by marker and `merge-smoke` does not reach by path. Without it the done-gate
# would call itself green while skipping every real-subprocess test in tests/.
ci_integration() {
    echo "[ci-local] integration (pytest --ignore=tests/e2e -m integration)" >&2
    "$PY" -m pytest --ignore=tests/e2e -m integration -q ${@+"$@"}
}

ci_coverage() {
    echo "[ci-local] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -m pytest --ignore=tests/e2e/ \
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
    "$PY" -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q ${@+"$@"}
}

# Offline and instant, so unlike version-skew it belongs in the `all` bundle.
ci_dependency_floor() {
    echo "[ci-local] dependency-floor (pins vs workspace versions)" >&2
    "$PY" scripts/check_dependency_floor.py
}

# Offline and instant, like dependency-floor — so it belongs in `all` too.
ci_silent_fallback() {
    echo "[ci-local] silent-fallback (broad handlers nothing can escape from)" >&2
    "$PY" scripts/check_silent_fallback.py
}

# The full maintainer tier (~25 min). Excluded from `all`; this is the selection
# the master pre-push gate runs, kept here so `make e2e` cannot mean something
# narrower than the gate that guards the push (HATS-1372).
ci_e2e() {
    echo "[ci-local] e2e (integration + smoke, quarantine excluded)" >&2
    "$PY" -m pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ -q ${@+"$@"}
}

# HATS-1137: the composition of the `edge:review--done` quality gate, and the
# ONE place it is configured. `maintainer-quality-gate/hooks/done-gate.sh --run`
# executes this stage and marks the SHA on green; changing what "green enough to
# be done" means is an edit HERE, never in the gate script.
#
# Excluded from `all`: `all` is the pre-push bundle and already runs `coverage`,
# which collects the same non-e2e integration tests without a marker filter.
ci_done_gate() {
    echo "[ci-local] done-gate (lint -> unit -> integration -> merge-smoke)" >&2
    local stage rc
    for stage in lint unit integration merge-smoke; do
        "ci_${stage//-/_}" || {
            rc=$?
            echo "[ci-local] done-gate: stage '$stage' FAILED (rc=$rc) — stopping here" >&2
            return "$rc"
        }
    done
    echo "[ci-local] done-gate: every stage green" >&2
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
    done-gate) ci_done_gate ;;
    security) ci_security ${@+"$@"} ;;
    merge-smoke) ci_merge_smoke ${@+"$@"} ;;
    dependency-floor) ci_dependency_floor ;;
    silent-fallback) ci_silent_fallback ;;
    e2e) ci_e2e ${@+"$@"} ;;
    version-skew) ci_version_skew ${@+"$@"} ;;
    all)
        # security is intentionally omitted — pip-audit is env-scoped (see NOTE).
        ci_lint
        ci_dependency_floor
        ci_silent_fallback
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[ci-local] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        echo "[ci-local] unknown stage: $stage" >&2
        echo "  stages: lint | unit | integration | coverage | security | merge-smoke | e2e | done-gate | dependency-floor | silent-fallback | version-skew | all" >&2
        exit 2
        ;;
esac
