"""Worktree-spawning scaffolding shared by the ``wt exec`` e2e tests (HATS-1205).

Pre-existing ``wt exec`` e2e files each carry their own copy; this module is for
the HATS-1205 additions so one scaffold serves four tests.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PLAN = """# Plan
## Requirements
do the thing
## Scope & Out-of-scope
in: thing; out: other
## Steps
1. thing
## Verification Protocol
run it
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def child_env(repo_root: Path) -> dict[str, str]:
    """Subprocess env pointed at the checkout under test.

    AI_HATS_LIBRARY_ROOT alongside PYTHONPATH (HATS-826): plain PYTHONPATH=src
    alone hits the HATS-685 vanished-roles trap.
    """
    from _helpers.env import checkout_pythonpath

    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    env["AI_HATS_LIBRARY_ROOT"] = str(
        repo_root / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    )
    return env


def ai_hats(
    binary: Path, *args: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120
    )


def tracker_tasks(root: Path) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"


def worktree_branches(main: Path) -> dict[str, Path]:
    """Map branch-name -> path for every linked ai-hats worktree."""
    out = git(main, "worktree", "list", "--porcelain").stdout
    branches: dict[str, Path] = {}
    cur_path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            p = Path(line[len("worktree ") :])
            cur_path = p if "ai-hats-wt" in line else None
        elif line.startswith("branch ") and cur_path is not None:
            branches[line[len("branch ") :].removeprefix("refs/heads/")] = cur_path
    return branches


def init_repo(main: Path) -> None:
    (main / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    git(main, "init", "-b", "master")
    git(main, "config", "user.email", "t@e")
    git(main, "config", "user.name", "T")
    git(main, "add", "-A")
    git(main, "commit", "-m", "init", "--allow-empty")


def spawn_worktree(binary: Path, main: Path, task_id: str, env: dict[str, str]) -> None:
    assert ai_hats(binary, "task", "create", task_id, "--id", task_id, cwd=main, env=env).returncode == 0
    assert ai_hats(binary, "task", "transition", task_id, "plan", cwd=main, env=env).returncode == 0
    (tracker_tasks(main) / task_id / "plan.md").write_text(PLAN)
    r = ai_hats(binary, "task", "transition", task_id, "execute", cwd=main, env=env)
    assert r.returncode == 0, r.stderr


def two_worktrees(binary: Path, main: Path, env: dict[str, str]) -> tuple[str, Path]:
    """Spawn two worktrees; return (branch, path) of the lexically first.

    Two, not one: with a sole worktree the resolver's convenience path finds it
    regardless of cwd, which would mask what these tests assert.
    """
    init_repo(main)
    spawn_worktree(binary, main, "HATS-1", env)
    spawn_worktree(binary, main, "HATS-2", env)
    branches = worktree_branches(main)
    assert len(branches) >= 2, f"expected two linked worktrees, got {branches}"
    branch = sorted(branches)[0]
    return branch, branches[branch]


def last_line(res: subprocess.CompletedProcess[str]) -> str:
    """Last stdout line — `git rev-parse --show-prefix` prints '' at the root."""
    lines = res.stdout.strip().splitlines()
    return lines[-1] if lines else ""
