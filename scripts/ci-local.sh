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
#   scripts/ci-local.sh            # local bundle: lint unit coverage merge-smoke
#   scripts/ci-local.sh lint       # one stage (used by the matching CI job)
#   scripts/ci-local.sh coverage   # the stage that was the sole failing executor
#   scripts/ci-local.sh security   # CI-only stage; env-scoped (see NOTE below)
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

PY="${PYTHON:-python}"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

ci_lint() {
    echo "[ci-local] lint (ruff)" >&2
    "$PY" -m ruff check .
}

ci_unit() {
    echo "[ci-local] unit (pytest -m 'not integration')" >&2
    "$PY" -m pytest -m "not integration" -q
}

ci_coverage() {
    echo "[ci-local] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -m pytest --ignore=tests/e2e/ \
        --cov=ai_hats \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=78 \
        -q
}

ci_security() {
    echo "[ci-local] security (bandit + pip-audit)" >&2
    "$PY" -m bandit -r src/ -ll -q
    "$PY" -m pip_audit
}

ci_merge_smoke() {
    echo "[ci-local] merge-smoke (curated e2e subset)" >&2
    "$PY" -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q
}

stage="${1:-all}"
case "$stage" in
    lint) ci_lint ;;
    unit) ci_unit ;;
    coverage) ci_coverage ;;
    security) ci_security ;;
    merge-smoke) ci_merge_smoke ;;
    all)
        # security is intentionally omitted — pip-audit is env-scoped (see NOTE).
        ci_lint
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[ci-local] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        echo "[ci-local] unknown stage: $stage" >&2
        echo "  stages: lint | unit | coverage | security | merge-smoke | all" >&2
        exit 2
        ;;
esac
