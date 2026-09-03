"""Contracts for the server-side quality-gate jobs."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_full_e2e_job_uses_the_merge_smoke_environment_and_canonical_stage():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    merge_smoke = jobs["merge-smoke"]
    e2e = jobs["e2e"]

    assert e2e["runs-on"] == merge_smoke["runs-on"] == "ubuntu-latest"
    assert e2e["steps"][0]["uses"] == merge_smoke["steps"][0]["uses"]
    assert e2e["steps"][0]["with"] == {"fetch-depth": 0}
    assert e2e["steps"][1] == merge_smoke["steps"][1]
    assert _step(e2e, "Install package + dev deps") == _step(
        merge_smoke, "Install package + dev deps"
    )
    assert _step(e2e, "Configure git identity (e2e tests create/commit projects)") == _step(
        merge_smoke, "Configure git identity (e2e tests create/commit projects)"
    )

    run = _step(e2e, "Run full e2e tier")
    # A stage runs bare (HATS-1878): the parallelism CI wants is pytest's own
    # environment variable, not argv the dispatcher would have to forward.
    assert run["env"] == {
        "AI_HATS_E2E_REQUIRE_VENV": "1",
        "PYTEST_ADDOPTS": "-n 8 --dist=loadgroup",
    }
    assert run["run"] == "bash scripts/gates.sh e2e"
