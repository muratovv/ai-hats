"""e2e (HATS-1174)

flow:   a developer initializing project configuration when ~/.gemini directory is
        present
cmds:
    ai-hats self init
expect: agy provider is automatically detected from ~/.gemini directory and alias gemini
        resolves to agy
why:    without provider auto-detection, users with gemini config dirs cannot run agy
        sessions without explicit configuration
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath

pytestmark = pytest.mark.integration


def test_agy_detected_via_gemini_home_dir(repo_root: Path, tmp_path: Path):
    """Verify that _detected_providers detects agy when ~/.gemini exists."""
    home_dir = tmp_path / "fake_home"
    (home_dir / ".gemini").mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)

    cmd = [
        # HATS-1826 folded agy into ai-hats: the `ai_hats.providers` entry point
        # now ships in the integrator's own metadata, so the interpreter that has
        # ai-hats installed IS the registry — no synthesised dist-info.
        sys.executable,
        "-c",
        "from ai_hats.cli.assembly import _detected_providers; "
        "from ai_hats.surface_registry import get_surface; "
        "detected = _detected_providers(); "
        "assert 'agy' in detected, f'agy not in {detected}'; "
        "p = get_surface('gemini'); "
        "assert p.name == 'agy', f'expected agy, got {p.name}'",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
