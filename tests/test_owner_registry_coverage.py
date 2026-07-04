"""HATS-911 coverage guard — every mechanism materializing outside
``<ai_hats_dir>`` is a registered living owner the sweeper can vouch for.

Justified-whitelist style (``test_no_direct_compose_outside_facade.py``):
adding a materializer without registering it — or registering one without
justifying it here — is a drift signal, not a formality. The registry is
the sweeper's liveness predicate: an unregistered materializer's marker
would be swept as dead on the next consumer bump.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_fresh_interpreter_liveness_before_sweep():
    """Import-order must never decide liveness (HATS-910 review carry-over):
    a generic sweep in a fresh interpreter has to see every living owner
    registered BEFORE any is_living() check, or it wrong-sweeps a live surface.
    """
    code = (
        "from ai_hats.sweeper import default_surfaces\n"
        "from ai_hats.owners import living_owners\n"
        "default_surfaces()\n"
        "missing = {'git-hooks', 'runtime-hooks'} - living_owners()\n"
        "assert not missing, f'unregistered living owners: {missing}'\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "src"),
            str(REPO_ROOT / "packages" / "ai-hats-core" / "src"),
        ]
    )
    res = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
