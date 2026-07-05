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

# Every mechanism materializing outside <ai_hats_dir> + WHY it writes there.
# New materializer: register_owner() at import time AND add it here — the
# sweeper treats a marker with an unregistered owner_key as dead.
MATERIALIZERS: dict[str, str] = {
    "git-hooks": (
        ".githooks/ dispatchers + <event>.d/ scripts — git can only exec "
        "hooks from the repo working tree (hooks_manager.install_git_hooks)"
    ),
    "runtime-hooks": (
        ".claude/settings.json managed hook entries — the harness reads its "
        "own settings file, not <ai_hats_dir> (providers.ClaudeProvider)"
    ),
}


def _fresh_living_owners() -> set[str]:
    """living_owners() as a FRESH interpreter sees them — immune to this
    process's registry snapshots/resets from earlier tests."""
    code = (
        "from ai_hats.sweeper import default_surfaces\n"
        "from ai_hats.owners import living_owners\n"
        "default_surfaces()\n"
        "print('\\n'.join(sorted(living_owners())))\n"
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
    return {line for line in res.stdout.splitlines() if line}


def test_fresh_interpreter_liveness_before_sweep():
    """Import-order must never decide liveness (HATS-910 review carry-over):
    a generic sweep in a fresh interpreter has to see every living owner
    registered BEFORE any is_living() check, or it wrong-sweeps a live surface.
    """
    missing = {"git-hooks", "runtime-hooks"} - _fresh_living_owners()
    assert not missing, f"unregistered living owners: {missing}"


def test_materializer_whitelist_matches_registry():
    """Bijection: an unregistered materializer gets wrong-swept at consumer
    bump; a registered owner without justification is registry drift."""
    living = _fresh_living_owners()
    unregistered = set(MATERIALIZERS) - living
    unjustified = living - set(MATERIALIZERS)
    assert not unregistered, (
        f"materializers not registered as living owners: {unregistered} — "
        "register_owner() at import time of the writing module"
    )
    assert not unjustified, (
        f"registered owners missing from MATERIALIZERS: {unjustified} — "
        "add the owner_key with a justification for writing outside <ai_hats_dir>"
    )


def test_every_justification_non_empty():
    for key, justification in MATERIALIZERS.items():
        assert justification.strip(), f"empty justification for {key!r}"


def test_every_surface_location_has_adapter():
    """Each adoption-table location must be one of the sweeper's known
    adapter types; dead ProcSurface owners must never claim to be living."""
    from ai_hats import sweeper

    surfaces = sweeper.default_surfaces()
    keys = [s.owner_key for s in surfaces]
    assert len(keys) == len(set(keys)), f"duplicate owner_key in surfaces: {keys}"
    for surface in surfaces:
        assert isinstance(
            surface,
            (
                sweeper.LineManifestSurface,
                sweeper.SettingsTagsSurface,
                sweeper.ProcSurface,
            ),
        ), surface
        assert surface.owner_key.strip(), surface
    proc_owners = {
        s.owner_key for s in surfaces if isinstance(s, sweeper.ProcSurface)
    }
    overlap = proc_owners & set(MATERIALIZERS)
    assert not overlap, f"ProcSurface is for DEAD mechanisms, but {overlap} is listed living"
