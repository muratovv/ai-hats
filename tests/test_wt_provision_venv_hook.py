"""Unit tests for HATS-1291: the worktree-venv wt_in provisioning hook.

Runs the real script against a stub `uv` on PATH, so the tests assert the
hook's contract (where it provisions, what it installs, when it declines)
without paying a real resolve.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SKILL_DIR = (
    Path(__file__).parent.parent
    / "packages"
    / "ai-hats-library"
    / "src"
    / "ai_hats_library"
    / "usage"
    / "skills"
    / "worktree-venv"
)
HOOK_PATH = SKILL_DIR / "hooks" / "provision-venv.sh"

# Stub uv: records argv per call, and materializes .venv/bin/python on `uv venv`
# so the script's own idempotence probe sees a real venv afterwards.
_UV_STUB = """#!/usr/bin/env bash
echo "$@" >> "{calls}"
if [[ "${{1:-}}" == "venv" ]]; then
    target="${{2:-.venv}}"
    mkdir -p "$target/bin"
    printf '#!/bin/sh\\nexit 0\\n' > "$target/bin/python"
    chmod +x "$target/bin/python"
fi
exit 0
"""


def _make_worktree(tmp_path: Path, *, packages: tuple[str, ...] = ()) -> Path:
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    (worktree / "pyproject.toml").write_text("[project]\nname = 'root'\n")
    for pkg in packages:
        pkg_dir = worktree / "packages" / pkg
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "pyproject.toml").write_text(f"[project]\nname = '{pkg}'\n")
    return worktree


def _run_hook(
    worktree: Path, *, path_dir: Path | None, project_dir: Path
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AI_HATS_WORKTREE_PATH"] = str(worktree)
    env["AI_HATS_PROJECT_DIR"] = str(project_dir)
    env["AI_HATS_BRANCH_NAME"] = "task/x"
    env["AI_HATS_EVENT"] = "wt_in"
    # The runner invokes hooks with cwd=project_dir (MAIN), never the worktree.
    env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin" if path_dir else "/usr/bin:/bin"
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [str(HOOK_PATH)],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _install_uv_stub(path_dir: Path, calls: Path) -> None:
    path_dir.mkdir(parents=True, exist_ok=True)
    uv = path_dir / "uv"
    uv.write_text(_UV_STUB.format(calls=calls))
    uv.chmod(0o755)


def test_hook_provisions_the_worktree_not_the_project_dir(tmp_path: Path) -> None:
    """The venv lands in AI_HATS_WORKTREE_PATH, though cwd is the main checkout."""
    worktree = _make_worktree(tmp_path, packages=("pkg-a", "pkg-b"))
    project_dir = tmp_path / "main"
    project_dir.mkdir()
    calls = tmp_path / "uv-calls.txt"
    _install_uv_stub(tmp_path / "bin", calls)

    result = _run_hook(worktree, path_dir=tmp_path / "bin", project_dir=project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text().splitlines()
    assert any(line.startswith("venv") for line in recorded), recorded
    install = next(line for line in recorded if line.startswith("pip install"))
    # Root project plus every discovered sub-package, all editable.
    assert "-e ." in install or "-e .[dev]" in install, install
    assert "packages/pkg-a" in install and "packages/pkg-b" in install, install
    assert not (project_dir / ".venv").exists(), "provisioned the main checkout"


def _make_venv(worktree: Path, *, gutted: bool = False) -> Path:
    """A venv the readiness probe accepts, or the skeleton a tmp sweep leaves.

    The OS sweeper deletes FILES and keeps directories, so the gutted form is a
    live ``bin/python`` symlink over a ``*.dist-info`` with no ``RECORD`` in it.
    """
    venv = worktree / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
    (venv / "bin" / "python").chmod(0o755)
    (venv / "pyvenv.cfg").write_text("home = /usr\n")
    dist_info = venv / "lib" / "python3.13" / "site-packages" / "root-1.0.dist-info"
    dist_info.mkdir(parents=True)
    if not gutted:
        (dist_info / "RECORD").write_text("root/__init__.py,,\n")
    return venv


def test_hook_is_idempotent(tmp_path: Path) -> None:
    """A worktree that already has a usable venv is left alone — re-runs are free."""
    worktree = _make_worktree(tmp_path)
    _make_venv(worktree)
    project_dir = tmp_path / "main"
    project_dir.mkdir()
    calls = tmp_path / "uv-calls.txt"
    _install_uv_stub(tmp_path / "bin", calls)

    result = _run_hook(worktree, path_dir=tmp_path / "bin", project_dir=project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls.exists(), f"uv was invoked despite an existing venv: {calls.read_text()}"


def test_hook_rebuilds_a_venv_the_tmp_sweeper_gutted(tmp_path: Path) -> None:
    """`-x bin/python` alone read a gutted venv as present, so the hook skipped it
    and the damage surfaced later as a ModuleNotFoundError from whatever ran next."""
    worktree = _make_worktree(tmp_path)
    _make_venv(worktree, gutted=True)
    project_dir = tmp_path / "main"
    project_dir.mkdir()
    calls = tmp_path / "uv-calls.txt"
    _install_uv_stub(tmp_path / "bin", calls)

    result = _run_hook(worktree, path_dir=tmp_path / "bin", project_dir=project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unusable" in result.stdout, result.stdout
    recorded = calls.read_text().splitlines()
    assert any(line.startswith("venv") for line in recorded), recorded
    assert not (worktree / ".venv" / "lib").exists(), "the gutted tree was reused"


def test_hook_declines_without_uv(tmp_path: Path) -> None:
    """No uv on PATH — warn-continue (exit 0), never a failed worktree create."""
    worktree = _make_worktree(tmp_path)
    project_dir = tmp_path / "main"
    project_dir.mkdir()

    result = _run_hook(worktree, path_dir=None, project_dir=project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "uv not found" in result.stdout
    assert not (worktree / ".venv").exists()


def test_hook_declines_on_a_non_python_worktree(tmp_path: Path) -> None:
    """No root pyproject.toml — not ours to provision."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    project_dir = tmp_path / "main"
    project_dir.mkdir()
    calls = tmp_path / "uv-calls.txt"
    _install_uv_stub(tmp_path / "bin", calls)

    result = _run_hook(worktree, path_dir=tmp_path / "bin", project_dir=project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls.exists()
    assert not (worktree / ".venv").exists()
