"""e2e (HATS-1498)

flow:   a maintainer runs the pre-push gate, which must refuse the push when
        tests/e2e/CATALOG.md no longer matches the flow blocks it is rendered
        from
cmds:
    bash scripts/ci-local.sh e2e-catalog     # exit 0 while the catalog is current
    bash scripts/ci-local.sh no-such-stage   # exit 2, and the usage names the stage
expect: the stage is reachable through the dispatcher, announces itself as
        `[ci-local] e2e-catalog`, and exits 0 on a clean tree; an unknown stage
        exits 2 and lists `e2e-catalog` among the stages it knows
why:    the checker is only a gate if `ci-local.sh` actually dispatches to it —
        `check_dependency_floor.py` sat outside this same ratchet from HATS-1399
        to HATS-1373, a gate script that was silently gating nothing
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ci-local.sh", name],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_gate_dispatches_to_the_catalog_check():
    done = _stage("e2e-catalog")
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "[ci-local] e2e-catalog" in combined, combined
    assert "[e2e-catalog] current" in combined, combined


def test_unknown_stage_lists_the_catalog_stage():
    """Deleting the `case` branch drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    assert "e2e-catalog" in combined, combined
