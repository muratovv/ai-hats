"""E2E: a wheel install materialises NO ``bin/ai-hats`` shadow (HATS-790, Alt 5).

Value under test: the ``[project.scripts] ai-hats = "ai_hats.cli:main_entry"``
console-script generator was removed so that NO venv depending on ``ai-hats``
materialises a ``bin/ai-hats`` that direnv could prepend ahead of the host
launcher (``~/.local/bin/ai-hats``) and run stale code. The package is invoked
exclusively via ``python -m ai_hats``.

This builds the real wheel from the repo and installs it into a throwaway venv
under ``tmp_path``, then asserts:

  1. NO ``<venv>/bin/ai-hats`` exists (the shadow generator is gone), and
  2. ``<venv>/bin/python -m ai_hats --version`` exits 0 (the module entry works).

Fail-under-revert (per ``dev_rule_e2e_gate`` §4): re-adding the
``[project.scripts] ai-hats = ...`` table to ``pyproject.toml`` makes the wheel
install drop ``<venv>/bin/ai-hats`` again → assertion #1 fails. Real
``uv build``/``python -m build`` + real ``uv``/``pip`` install — no stubs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.workspace import build_workspace_member_wheels  # noqa: E402

pytestmark = pytest.mark.install_heavy  # real wheel build + install at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _have(cmd: str) -> bool:
    import shutil

    return shutil.which(cmd) is not None


def _build_wheel(out_dir: Path, env: dict) -> Path:
    """Build the ai-hats wheel into ``out_dir``; return its path.

    Prefers ``uv build`` (the repo's release engine, HATS-765); falls back to
    ``python -m build``. Skips loudly if neither toolchain is available.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if _have("uv"):
        cmd = ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)]
    else:
        # `python -m build` needs the `build` package; if it is absent this
        # raises CalledProcessError and the caller skips.
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), str(REPO_ROOT)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"wheel build unavailable / failed:\n{proc.stdout}\n{proc.stderr}")
    wheels = sorted(out_dir.glob("ai_hats-*.whl"))
    if not wheels:
        pytest.skip(f"no wheel produced in {out_dir}:\n{proc.stdout}\n{proc.stderr}")
    # HATS-898: ai-hats needs unpublished ai-hats-core/ai-hats-wt — build them
    # into the same dir so the install resolves them via --find-links.
    build_workspace_member_wheels(REPO_ROOT, out_dir, env)
    return wheels[-1]


@pytest.mark.integration
def test_wheel_install_has_no_console_script_and_module_runs(tmp_path: Path) -> None:
    """Installing the built wheel produces NO bin/ai-hats; `python -m ai_hats` runs."""
    if not _have("uv"):
        pytest.skip("uv is required to provision the throwaway venv")

    env = os.environ.copy()
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)  # do not leak the editable src onto the install venv

    wheel = _build_wheel(tmp_path / "dist", env)

    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", "--python", "3.11", str(venv)],
        check=True, capture_output=True, text=True, env=env, timeout=120,
    )
    venv_python = venv / "bin" / "python"
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python),
         "--find-links", str(wheel.parent), str(wheel)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert install.returncode == 0, f"wheel install failed:\n{install.stdout}\n{install.stderr}"

    # 1. NO console-script shadow (the heart of HATS-790).
    assert not (venv / "bin" / "ai-hats").exists(), (
        "bin/ai-hats console script must NOT be materialised by the wheel install "
        "(HATS-790: the [project.scripts] generator was removed)"
    )

    # 2. The module entry point works.
    run = subprocess.run(
        [str(venv_python), "-m", "ai_hats", "--version"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert run.returncode == 0, (
        f"`python -m ai_hats --version` failed:\n{run.stdout}\n{run.stderr}"
    )
    # Click renders ``<prog_name>, version <X>`` — prog_name is ``python -m ai_hats``
    # (underscore). Assert the version line, tolerant of the ai_hats/ai-hats spelling.
    out = (run.stdout + run.stderr).lower()
    assert "version" in out and ("ai_hats" in out or "ai-hats" in out), (
        f"--version output unexpected:\n{run.stdout}\n{run.stderr}"
    )
