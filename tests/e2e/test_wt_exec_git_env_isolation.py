"""e2e (HATS-887)

flow:   a developer executing git commands via wt exec with ambient GIT_DIR environment
        variables
cmds:
    # with ambient GIT_DIR set to main repository
    ai-hats wt exec -- git rev-parse --absolute-git-dir
expect: ambient GIT_DIR is stripped so inner git resolves the worktree git directory
why:    wt exec must un-poison git environment variables to prevent operations on main
        checkout
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.wt import spawn_worktree

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _ai_hats(binary: Path, *args: str, cwd: Path, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=env if env is not None else {**os.environ},
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


def test_wt_exec_strips_ambient_git_env(tmp_project, tmp_path):
    main = tmp_project
    binary = main.ai_hats_binary

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # A managed worktree is born on `transition execute`.
    spawn_worktree(main.path, "HATS-1", {**os.environ})
    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "a worktree must exist after execute"

    main_git_dir = (main.path / ".git").resolve()

    # Issue from MAIN with an ambient GIT_DIR pinned at the main repo. Resolution
    # (list_active) is filesystem-based, so it is unaffected; only the inner
    # `git` spawned by `wt exec` can still be poisoned — which the fix prevents.
    env = {**os.environ, "GIT_DIR": str(main_git_dir), "GIT_WORK_TREE": str(main.path.resolve())}
    res = _ai_hats(
        binary,
        "wt",
        "exec",
        "--",
        "git",
        "rev-parse",
        "--absolute-git-dir",
        cwd=main.path,
        env=env,
    )
    assert res.returncode == 0, f"wt exec failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"

    resolved = Path(res.stdout.strip().splitlines()[-1]).resolve()
    assert resolved != main_git_dir, (
        "🐛 HATS-887 REGRESSION: `wt exec` leaked the ambient GIT_DIR — the inner "
        f"git resolved the MAIN repo {main_git_dir}, not the worktree gitdir."
    )
    assert "worktrees" in str(resolved), (
        f"inner git should resolve the linked-worktree gitdir, got {resolved}"
    )
