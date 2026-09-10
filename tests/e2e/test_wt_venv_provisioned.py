"""e2e (HATS-1242)

flow:   a developer creating a worktree in a project requiring isolated python
        environments
cmds:
    ai-hats wt create task/probe
expect: a virtual environment is provisioned inside worktree .venv and imports
        worktree source
why:    worktrees must provision isolated venvs to prevent importing main repository
        packages"""

from __future__ import annotations
from _helpers.git import git as _git

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def _run(cmd, *, cwd, env, timeout=300, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _wt_path(project: Path, branch: str) -> Path | None:
    out = _git(project, "worktree", "list", "--porcelain").stdout
    cur: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and cur is not None:
            if line.strip().endswith("/" + branch):
                return cur
    return None


def _init(launcher: Path, env: dict, project: Path) -> None:
    """A minimal but REAL python project — the hook declines without a pyproject."""
    project.mkdir(parents=True, exist_ok=True)
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "pyproject.toml").write_text(PYPROJECT)
    pkg = project / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("ORIGIN = 'main'\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-m", "init")
    _run(
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "maintainer",
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=project,
        env=env,
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("uv") is None, reason="hook needs uv on PATH")
def test_wt_create_provisions_a_worktree_venv(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    _run([str(launcher), "wt", "create", "task/probe"], cwd=project, env=env)
    worktree = _wt_path(project, "task/probe")
    assert worktree is not None

    python = worktree / ".venv" / "bin" / "python"
    assert python.is_file(), (
        f"wt_in hook did not provision a venv in {worktree} "
        f"(contents: {sorted(p.name for p in worktree.iterdir())})"
    )

    # The point of the venv: its editable install must resolve to the WORKTREE.
    # Resolving to the main checkout is exactly the HATS-1242 failure.
    probe = _run(
        [str(python), "-c", "import demo, pathlib; print(pathlib.Path(demo.__file__).resolve())"],
        cwd=worktree,
        env=env,
    )
    resolved = Path(probe.stdout.strip()).resolve()
    assert resolved.is_relative_to(worktree.resolve()), (
        f"worktree venv imports demo from {resolved}, expected inside {worktree}"
    )
