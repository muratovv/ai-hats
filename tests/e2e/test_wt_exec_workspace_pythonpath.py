"""e2e (HATS-913)

flow:   a developer executing python commands in a worktree containing workspace
        packages
cmds:
    ai-hats wt exec -- python -c "import mypkg"
expect: packages/*/src directories within worktree are added to PYTHONPATH
why:    wt exec must thread workspace packages into PYTHONPATH for isolated package
        resolution"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_wt.env import PACKAGES_DIRNAME, SRC_DIRNAME

from _helpers.wt import spawn_worktree

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _child_env(repo_root: Path) -> dict[str, str]:
    """Subprocess env pointed at the checkout under test (worktree or main)."""
    from _helpers.env import checkout_pythonpath

    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    env["AI_HATS_LIBRARY_ROOT"] = str(
        repo_root / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    )
    return env


def _ai_hats(
    binary: Path, *args: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _find_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


def test_wt_exec_resolves_workspace_package_from_worktree(tmp_project, repo_root):
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    pkg = main.path / PACKAGES_DIRNAME / "mypkg" / SRC_DIRNAME / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init")

    spawn_worktree(main.path, "HATS-1", env)
    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "a worktree must exist after execute"

    res = _ai_hats(
        binary,
        "wt",
        "exec",
        "--",
        sys.executable,
        "-c",
        "import mypkg; print(mypkg.__file__)",
        cwd=main.path,
        env=env,
    )
    assert res.returncode == 0, (
        "🐛 HATS-913 REGRESSION: `wt exec` left packages/*/src off PYTHONPATH — "
        f"the worktree's workspace package is invisible:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )

    resolved = Path(res.stdout.strip().splitlines()[-1]).resolve()
    assert resolved.is_relative_to(wt.resolve()), (
        f"mypkg must resolve from the worktree {wt}, got {resolved}"
    )
