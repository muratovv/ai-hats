"""E2E: the real ``ai-hats-agy`` surface plugin is discovered by the ``ai-hats``
binary via the ``ai_hats.providers`` entry point (HATS-1093).

Mirrors ``test_cline_provider_discovery.py`` but drives the REAL
``ai_hats_agy.AgyProvider`` and the REAL entry-point declaration read from the
package's pyproject — so it fails under revert if the package drops its
``[project.entry-points."ai_hats.providers"]`` line (uninstall → ``agy`` gone).
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath

pytestmark = pytest.mark.integration

_AGY_PKG = "packages/surfaces/agy"


def _entry_point_body(repo_root: Path) -> str:
    """The real ``agy = ai_hats_agy:AgyProvider`` line, read from the package
    pyproject so a dropped entry point fails this test."""
    pyproject = tomllib.loads((repo_root / _AGY_PKG / "pyproject.toml").read_text())
    eps = pyproject["project"]["entry-points"]["ai_hats.providers"]
    return "\n".join(f"{name} = {target}" for name, target in eps.items())


def _write_dist_info(root: Path, ep_body: str) -> Path:
    """A synthetic installed dist advertising the real agy entry point."""
    root.mkdir(parents=True, exist_ok=True)
    dist_info = root / "ai_hats_agy-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: ai-hats-agy\nVersion: 0.1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(f"[ai_hats.providers]\n{ep_body}\n")
    return root


def test_agy_surface_is_discovered_by_the_binary(
    ai_hats_shim: Path, repo_root: Path, tmp_path: Path
):
    ep_body = _entry_point_body(repo_root)
    assert "agy = ai_hats_agy:AgyProvider" in ep_body  # guards the pyproject read

    dist_dir = _write_dist_info(tmp_path / "dist", ep_body)
    agy_src = str(repo_root / _AGY_PKG / "src")

    env = os.environ.copy()  # PYTHONPATH already scrubbed by _scrub_redirect_env
    env["PYTHONPATH"] = os.pathsep.join(
        [checkout_pythonpath(repo_root), agy_src, str(dist_dir)]
    )

    result = subprocess.run(
        [str(ai_hats_shim), "list", "providers"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "agy" in result.stdout, result.stdout
    # discovery augments, not replaces — the built-ins are still there
    assert "claude" in result.stdout, result.stdout
