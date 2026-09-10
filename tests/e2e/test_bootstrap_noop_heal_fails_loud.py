"""e2e (HATS-1359)

flow:   a developer running ai-hats when a missing package cannot be healed by package
        manager
cmds:
    python -m ai_hats config status
expect: bootstrap process exits cleanly with error status naming missing dependency
why:    without missing dep rechecks, no-op package installs cause infinite re-exec
        loops
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _site_packages(venv: Path) -> Path:
    cands = sorted((venv / "lib").glob("python*/site-packages"))
    assert cands, f"no site-packages found under {venv}"
    return cands[0]


@pytest.mark.integration
def test_bootstrap_or_die_fails_loud_on_noop_heal(tmp_path: Path) -> None:
    """A dep whose dist-info survives but whose module dir is gone → clean exit(1), no hang."""
    from _helpers.venv import build_launcher_venv, network_available, venv_unavailable

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build launcher venv")

    work = tmp_path / "wt"
    work.mkdir()
    try:
        _launcher, venv = build_launcher_venv(work, REPO_ROOT)
    except FileNotFoundError as exc:
        venv_unavailable(f"install-launcher.sh missing: {exc}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        venv_unavailable(f"launcher venv build failed/timed out: {exc}")

    venv_python = venv / "bin" / "python"
    pty_pkg = _site_packages(venv) / "ptyprocess"
    assert pty_pkg.is_dir(), f"ptyprocess not found at {pty_pkg}"
    shutil.rmtree(pty_pkg)  # dist-info (RECORD) survives — only the module is gone

    broken = subprocess.run(
        [str(venv_python), "-c", "import ptyprocess"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert broken.returncode != 0, "ptyprocess still importable after deletion — setup failed"

    env = os.environ.copy()
    env["UV_OFFLINE"] = "1"  # a real fetch would mask the no-op-heal premise

    result = subprocess.run(
        [str(venv_python), "-m", "ai_hats", "config", "status"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,  # the regression this guards against hangs, it isn't merely slow
    )
    assert result.returncode == 1, (
        f"expected a clean exit(1), got {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    assert "ptyprocess" in result.stderr
    assert "uv pip install" in result.stderr
