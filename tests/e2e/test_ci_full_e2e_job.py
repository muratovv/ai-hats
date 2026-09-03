"""e2e (HATS-1708)

flow:   a GitHub Actions runner executes the full e2e job declared in ci.yml
cmds:
    PYTEST_ADDOPTS="-n 8 --dist=loadgroup --collect-only" bash scripts/ci-local.sh e2e
expect: the versioned workflow command reaches the canonical dispatcher and
        successfully collects the full e2e selection
why:    a syntactically valid workflow can still name a missing stage or bypass
        the canonical dispatcher, leaving the advertised server-side gate inert
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


# HATS-1708
def test_full_e2e_job_drives_the_canonical_dispatcher():
    """The canonical full tier excludes live agy calls, not offline agy coverage."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    step = next(step for step in jobs["e2e"]["steps"] if step.get("name") == "Run full e2e tier")
    argv = shlex.split(step["run"])
    argv[0] = shutil.which(argv[0], path=os.environ.get("PATH")) or argv[0]

    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.update({key: str(value) for key, value in step.get("env", {}).items()})
    # A stage runs bare (HATS-1878): the collect-only switch rides the same
    # pytest variable the workflow uses for its parallelism.
    env["PYTEST_ADDOPTS"] = f"{env.get('PYTEST_ADDOPTS', '')} --collect-only".strip()
    result = subprocess.run(  # noqa: S603 — argv comes from the versioned workflow contract
        argv,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "[ci-local] e2e" in combined
    assert "test_ci_full_e2e_job.py::test_full_e2e_job_drives_the_canonical_dispatcher" in combined
    assert "test_agy_bypass.py::test_agy_bypasses_root_gemini_md" not in combined
    assert "test_clean_root_sentinel.py::test_agy_clean_root_sentinel" not in combined
    assert "test_edge_check_gate.py::test_the_gate_fires_inside_a_live_agy_session" not in combined
    assert "test_agy_session_recorded.py::test_agy_session_records_audit_and_usage" not in combined
    assert (
        "test_agy_session_recorded.py::test_agy_session_transcript_resolution_and_audit" in combined
    )
