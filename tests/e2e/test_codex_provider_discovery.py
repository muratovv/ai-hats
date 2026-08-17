"""e2e (HATS-1531)

flow:   a developer lists providers with the ai-hats-codex package installed
cmds:
    ai-hats list providers
expect: codex is discovered through the real package entry point alongside claude
why:    registry metadata alone cannot launch a surface; the distribution entry point
        must be visible to the shipped binary (HATS-1531)
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath

pytestmark = pytest.mark.integration

_CODEX_PKG = "packages/surfaces/codex"


def _entry_point_body(repo_root: Path) -> str:
    pyproject = tomllib.loads((repo_root / _CODEX_PKG / "pyproject.toml").read_text())
    eps = pyproject["project"]["entry-points"]["ai_hats.providers"]
    return "\n".join(f"{name} = {target}" for name, target in eps.items())


def _write_dist_info(root: Path, ep_body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    dist_info = root / "ai_hats_codex-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: ai-hats-codex\nVersion: 0.1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(f"[ai_hats.providers]\n{ep_body}\n")
    return root


def test_codex_surface_is_discovered_by_the_binary(
    ai_hats_shim: Path, repo_root: Path, tmp_path: Path
) -> None:
    # HATS-1531 acceptance: the new surface is real entry-point wiring, not
    # registry metadata that only makes it appear in the init picker.
    ep_body = _entry_point_body(repo_root)
    assert "codex = ai_hats_codex:CodexProvider" in ep_body

    dist_dir = _write_dist_info(tmp_path / "dist", ep_body)
    codex_src = str(repo_root / _CODEX_PKG / "src")

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([checkout_pythonpath(repo_root), codex_src, str(dist_dir)])

    result = subprocess.run(
        [str(ai_hats_shim), "list", "providers"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "codex" in result.stdout, result.stdout
    assert "claude" in result.stdout, result.stdout
