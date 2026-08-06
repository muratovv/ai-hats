"""E2E: ``pip install ai-hats-rack`` puts ``rack`` on PATH (HATS-1329).

This builds the real ``ai-hats-rack`` wheel and installs it into a bare venv,
then runs ``rack --help`` resolved on ``PATH`` and asserts exit code 0.

Fail-under-revert (per ``dev_rule_e2e_gate``): drop the ``[project.scripts]``
table from ``packages/ai-hats-rack/pyproject.toml`` -> the wheel install
materialises no ``<venv>/bin/rack`` -> the console file is absent and bare probe
fails. Real ``uv build`` + ``uv`` install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env
from _helpers.venv import network_available, venv_unavailable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_DIR = REPO_ROOT / "packages" / "ai-hats-rack"

pytestmark = pytest.mark.install_heavy


def _run(cmd, *, cwd, env, timeout):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{cmd} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_rack_console_script_resolves_on_path(tmp_path):
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build/install the rack wheel")

    env = clean_env()
    wheeldir = tmp_path / "wheels"

    _run(
        ["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(PACKAGE_DIR)],
        cwd=tmp_path,
        env=env,
        timeout=180,
    )
    assert sorted(wheeldir.glob("ai_hats_rack-*.whl")), "no ai-hats-rack wheel built"

    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.13", str(venv)], cwd=tmp_path, env=env, timeout=120)
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
            "--find-links",
            str(wheeldir),
            "ai-hats-rack",
        ],
        cwd=tmp_path,
        env=env,
        timeout=180,
    )

    console = venv / "bin" / "rack"
    assert console.is_file(), f"[project.scripts] must materialise {console}"

    probe_env = dict(env)
    probe_env["PATH"] = str(venv / "bin") + os.pathsep + probe_env.get("PATH", "")
    probe = subprocess.run(
        ["rack", "--help"],
        cwd=str(tmp_path),
        env=probe_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, f"`rack --help` failed:\n{probe.stderr}"
    assert "rack" in probe.stdout, probe.stdout
