"""e2e (HATS-1701)

flow:   a user launches Codex from an editable checkout whose surface package
        is not installed in the active environment
cmds:
    ai-hats -p codex -r maintainer
expect: the first launch installs the local Codex surface and starts Codex
        without falling through to the registry installer
why:    editable installation writes a path file that the running interpreter
        has not processed, so an in-process recheck otherwise misses the heal

flow:   a user launches Codex without its surface and package installation fails
cmds:
    ai-hats -p codex -r maintainer
expect: the command exits 2 with the installer diagnostic and no Python traceback
why:    subprocess stderr is the actionable installation failure, but a bare
        CalledProcessError hides it and the generic CLI path leaks a traceback
"""  # comment-length: allow — the four-field catalog contract has two user flows
# ruff: noqa: S101

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.editable_venv import build_editable_venv
from _helpers.env import clean_env

pytestmark = [pytest.mark.integration, pytest.mark.install_heavy]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_noneditable_venv(tmp_path: Path) -> Path:
    real_uv = shutil.which("uv")
    assert real_uv is not None
    venv = tmp_path / "noneditable-venv"
    subprocess.run(  # noqa: S603 - resolved uv binary, test-owned target
        [real_uv, "venv", str(venv), "--python", "3.11"],
        check=True,
        capture_output=True,
        timeout=300,
    )
    venv_python = venv / "bin" / "python"
    subprocess.run(  # noqa: S603 - resolved uv binary, local checkout input
        [real_uv, "pip", "install", "--python", str(venv_python), str(REPO_ROOT)],
        check=True,
        capture_output=True,
        timeout=600,
    )
    return venv_python


def _tool_shims(tmp_path: Path) -> Path:
    real_uv = shutil.which("uv")
    assert real_uv is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "uv"
    shim.write_text(
        f"""#!{sys.executable}
import os
import sys

args = sys.argv[1:]
if "-e" in args:
    os.execv({real_uv!r}, [{real_uv!r}, *args])
print("registry fallback forbidden", file=sys.stderr)
raise SystemExit(97)
"""
    )
    shim.chmod(0o755)
    codex = bin_dir / "codex"
    codex.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path

Path(os.environ["HATS1701_CODEX_RECORD"]).write_text("launched")
"""
    )
    codex.chmod(0o755)
    return bin_dir


def test_editable_codex_surface_loads_on_first_launch(tmp_path: Path) -> None:
    # HATS-1701: the healed surface must load before registry fallback.
    venv_python, checkout = build_editable_venv(tmp_path, REPO_ROOT)
    before = subprocess.run(  # noqa: S603 - test-created interpreter, literal argv
        [
            str(venv_python),
            "-c",
            "import importlib.util; print(importlib.util.find_spec('ai_hats_codex'))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert before.returncode == 0, before.stderr
    assert before.stdout.strip() == "None", "Codex surface must be absent before launch"

    env = clean_env()
    shim_dir = _tool_shims(tmp_path)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    record = tmp_path / "codex-launched"
    env["HATS1701_CODEX_RECORD"] = str(record)
    result = subprocess.run(  # noqa: S603 - test-created interpreter, literal argv
        [
            str(venv_python),
            "-m",
            "ai_hats",
            "-p",
            "codex",
            "-r",
            "maintainer",
        ],
        cwd=str(checkout),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    after = subprocess.run(  # noqa: S603 - test-created interpreter, literal argv
        [
            str(venv_python),
            "-c",
            "import importlib.util; print(importlib.util.find_spec('ai_hats_codex'))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert after.returncode == 0, after.stderr
    assert after.stdout.strip() != "None", "workspace heal did not install Codex surface"
    assert result.returncode == 0, (
        f"first launch failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert record.read_text() == "launched"
    assert "ProviderInstallationError" not in result.stderr, result.stderr


def test_surface_install_error_is_friendly(tmp_path: Path) -> None:
    venv_python = _build_noneditable_venv(tmp_path)
    env = clean_env()
    env["AI_HATS_SKIP_SELF_LOCATION_GUARD"] = "1"
    shim_dir = _tool_shims(tmp_path)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(  # noqa: S603 - test-created interpreter, literal argv
        [
            str(venv_python),
            "-m",
            "ai_hats",
            "-p",
            "codex",
            "-r",
            "maintainer",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 2, result.stderr
    assert "Failed to auto-install surface plugin 'codex'" in result.stderr
    assert "ai-hats-codex" in result.stderr
    assert "registry fallback forbidden" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
