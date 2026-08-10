"""e2e (HATS-1174)

flow:   a developer initializing project configuration when ~/.gemini directory is
        present
cmds:   ai-hats self init
expect: agy provider is automatically detected from ~/.gemini directory and alias gemini
        resolves to agy
why:    provider detection must auto-discover agy when gemini config directory exists in
        HOME
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
    pyproject = tomllib.loads((repo_root / _AGY_PKG / "pyproject.toml").read_text())
    eps = pyproject["project"]["entry-points"]["ai_hats.providers"]
    return "\n".join(f"{name} = {target}" for name, target in eps.items())


def _write_dist_info(root: Path, ep_body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    dist_info = root / "ai_hats_agy-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: ai-hats-agy\nVersion: 0.1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(f"[ai_hats.providers]\n{ep_body}\n")
    return root


def test_agy_detected_via_gemini_home_dir(repo_root: Path, tmp_path: Path):
    """Verify that _detected_providers detects agy when ~/.gemini exists."""
    ep_body = _entry_point_body(repo_root)
    dist_dir = _write_dist_info(tmp_path / "dist", ep_body)
    agy_src = str(repo_root / _AGY_PKG / "src")

    home_dir = tmp_path / "fake_home"
    (home_dir / ".gemini").mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = os.pathsep.join([checkout_pythonpath(repo_root), agy_src, str(dist_dir)])

    cmd = [
        "python3",
        "-c",
        "from ai_hats.cli.assembly import _detected_providers; "
        "from ai_hats.providers import get_provider; "
        "detected = _detected_providers(); "
        "assert 'agy' in detected, f'agy not in {detected}'; "
        "p = get_provider('gemini'); "
        "assert p.name == 'agy', f'expected agy, got {p.name}'",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
