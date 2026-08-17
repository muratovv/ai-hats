"""e2e (HATS-1498)

flow:   a maintainer runs the pre-push gate, which must route the catalog check
        through the `ci-local.sh` dispatcher rather than leave it unreachable
cmds:
    bash scripts/ci-local.sh e2e-catalog     # announces the stage it dispatched to
    bash scripts/ci-local.sh no-such-stage   # exit 2, and the usage names the stage
expect: the stage is reachable through the dispatcher and announces itself as
        `[ci-local] e2e-catalog`; an unknown stage exits 2 and lists
        `e2e-catalog` among the stages it knows — one list, derived from the
        `ci_*` functions, so both answers die together (HATS-1716)
why:    the checker is only a gate if `ci-local.sh` actually dispatches to it —
        `check_dependency_floor.py` sat outside this same ratchet from HATS-1399
        to HATS-1373, a gate script that was silently gating nothing. Whether the
        catalog is CURRENT belongs to the stage, not here: this runs against the
        live checkout while sibling workers write it (HATS-1714)
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
    """The announce IS the dispatch proof: an unwired stage exits 2 without it.

    Neither the exit code nor `[e2e-catalog] current` is asserted — both report
    the live tree's freshness, which a parallel sibling can change under us.
    """
    done = _stage("e2e-catalog")
    combined = done.stdout + done.stderr
    assert "[ci-local] e2e-catalog" in combined, combined
    assert done.returncode != 2, combined


def test_unknown_stage_lists_the_catalog_stage():
    """Deleting `ci_e2e_catalog` drops the stage from this list too.

    True since HATS-1716 and not before: the list was a hand-written string, so
    this passed on a dispatcher that had lost the stage entirely — measured.
    """
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    listed = [
        line.split(":", 1)[1].split()
        for line in combined.splitlines()
        if line.strip().startswith("stages:")
    ]
    # Membership, not substring: `ci_e2e_catalog_GONE` would list
    # `e2e-catalog-GONE` and satisfy a substring check while dispatching nothing.
    assert listed and "e2e-catalog" in listed[0], combined
