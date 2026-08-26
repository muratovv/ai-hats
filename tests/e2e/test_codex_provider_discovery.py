"""e2e (HATS-1531, HATS-1826)

flow:   a developer lists the providers a plain ai-hats install offers
cmds:
    ai-hats list providers
expect: codex is discovered through the entry point ai-hats declares for it and is
        displayed alongside claude
why:    a surface reaches the binary only through the `ai_hats.surface_registry` group;
        codex used to ship as its own distribution and HATS-1826 folded it into
        ai-hats, so a dropped declaration would silently un-ship the surface
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath

pytestmark = pytest.mark.integration

# HATS-1826 folded this surface out of its own distribution and into
# ai-hats, so the integrator's pyproject is now the declaration under test.
_SURFACE = "codex"
_DECLARED = "ai_hats.surfaces.codex.provider:CodexSurface"


def _declared_entry_point(repo_root: Path) -> str | None:
    """The real ``codex = ...`` declaration, read from the integrator's pyproject
    so a dropped entry point fails this test rather than this helper."""
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    return pyproject["project"]["entry-points"]["ai_hats.providers"].get(_SURFACE)


def test_codex_surface_is_discovered_by_the_binary(ai_hats_shim: Path, repo_root: Path):
    assert _declared_entry_point(repo_root) == _DECLARED, (
        f"ai-hats no longer declares {_SURFACE!r} under ai_hats.surface_registry"
    )

    env = os.environ.copy()  # PYTHONPATH already scrubbed by _scrub_redirect_env
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)

    result = subprocess.run(
        [str(ai_hats_shim), "list", "providers"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert _SURFACE in result.stdout, result.stdout
    # discovery augments, not replaces — the sibling surfaces are still there
    assert "claude" in result.stdout, result.stdout
