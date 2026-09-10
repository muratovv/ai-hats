"""e2e (HATS-1872)

flow:   a maintainer runs the pre-push gate, which must route the env-reference
        check through the `gates.sh` dispatcher rather than leave it unreachable
cmds:
    bash scripts/gates.sh env-reference    # announces the stage it dispatched to
    bash scripts/gates.sh no-such-stage    # exit 2, and the usage names the stage
expect: the stage is reachable through the dispatcher and announces itself as
        `[gates] env-reference`; an unknown stage exits 2 and lists
        `env-reference` among the stages it knows — one list, derived from the
        `ci_*` functions, so both answers die together
why:    a generated page only stays current if something refuses it once it is
        not, and the refusal is only a gate if `gates.sh` dispatches to it.
        Whether the page is CURRENT belongs to the stage, not here: this runs
        against the live checkout while sibling workers write it (HATS-1714)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gates]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/gates.sh", name],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_gate_dispatches_to_the_env_reference_check():
    """The announce IS the dispatch proof: an unwired stage exits 2 without it.

    Neither the exit code nor `[env-reference] current` is asserted — both report
    the live tree's freshness, which a parallel sibling can change under us.
    """
    done = _stage("env-reference")
    combined = done.stdout + done.stderr
    assert "[gates] env-reference" in combined, combined
    assert done.returncode != 2, combined


def test_unknown_stage_lists_the_env_reference_stage():
    """Deleting `ci_env_reference` drops the stage from this list too."""
    missing = _stage("no-such-stage")
    combined = missing.stdout + missing.stderr
    assert missing.returncode == 2, combined
    listed = [
        line.split(":", 1)[1].split()
        for line in combined.splitlines()
        if line.strip().startswith("stages:")
    ]
    # Membership, not substring: `ci_env_reference_GONE` would list
    # `env-reference-GONE` and satisfy a substring check while dispatching nothing.
    assert listed and "env-reference" in listed[0], combined
