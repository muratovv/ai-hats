"""e2e (HATS-1646)

flow:   a maintainer runs the pre-push bundle, which must refuse the push when a
        citation into an ADR no longer resolves, or when one ADR number names
        two files
cmds:
    bash scripts/ci-local.sh adr-integrity   # exit 0 while the corpus is intact
    bash scripts/ci-local.sh no-such-stage   # exit 2, and the usage names the stage
expect: the stage is reachable through the dispatcher, announces itself as
        `[ci-local] adr-integrity`, exits 0 on a clean corpus and states on every
        run what it does NOT cover; an unknown stage exits 2 and lists
        `adr-integrity` among the stages it knows
why:    a checker is only a gate if `ci-local.sh` actually dispatches to it —
        `check_dependency_floor.py` sat outside this same ratchet from HATS-1399
        to HATS-1373, silently gating nothing. HATS-1646 adds a checker whose
        absence is equally invisible: its defects (a citation into a section
        that does not exist, one ADR number naming two files) rot green.
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


def test_gate_dispatches_to_the_adr_check():
    done = _stage("adr-integrity")
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "[ci-local] adr-integrity" in combined, combined
    assert "[adr-integrity] ok:" in combined, combined


def test_the_run_states_what_it_does_not_cover():
    """A narrowing nobody is told about reads as green.

    HATS-1646 fixed the check's scope twice while writing it (numeric `§N`
    sections, prose section names), so the uncovered list is a contract, not a
    courtesy: silence here would be indistinguishable from coverage.
    """
    run = _stage("adr-integrity")
    combined = run.stdout + run.stderr
    assert "not covered:" in combined, combined


def test_unknown_stage_lists_the_adr_stage():
    """Deleting the `case` branch drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    assert "adr-integrity" in combined, combined
