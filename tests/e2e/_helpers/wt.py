"""Worktree-spawning scaffolding shared by the ``wt exec`` e2e tests (HATS-1205).

Pre-existing ``wt exec`` e2e files each carry their own copy; this module is for
the HATS-1205 additions so one scaffold serves four tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _helpers.git import git as _git, init_repo as _init_repo

git = _git

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


def init_repo(main: Path) -> None:
    _init_repo(main, branch="master", harden=True)


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


def rack(*args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the backlog CLI (HATS-1263). This tier has no ``rack`` console script,
    but ``child_env`` puts the checkout on PYTHONPATH, so ``python -m`` reaches it."""
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
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


def spawn_worktree(main: Path, task_id: str, env: dict[str, str]) -> None:
    assert rack("create", task_id, "--id", task_id, cwd=main, env=env).returncode == 0
    assert rack("transition", task_id, "plan", cwd=main, env=env).returncode == 0
    (tracker_tasks(main) / task_id / "plan.md").write_text(PLAN)
    # plan->execute is consent-gated on rack; these spawns are scaffolding.
    r = rack("transition", task_id, "execute", cwd=main, env={**env, "AI_HATS_PLAN_ACK": "1"})
    assert r.returncode == 0, r.stderr


def two_worktrees(main: Path, env: dict[str, str]) -> tuple[str, Path]:
    """Spawn two worktrees; return (branch, path) of the lexically first.

    Two, not one: with a sole worktree the resolver's convenience path finds it
    regardless of cwd, which would mask what these tests assert.
    """
    init_repo(main)
    spawn_worktree(main, "HATS-1", env)
    spawn_worktree(main, "HATS-2", env)
    branches = worktree_branches(main)
    assert len(branches) >= 2, f"expected two linked worktrees, got {branches}"
    branch = sorted(branches)[0]
    return branch, branches[branch]


def last_line(res: subprocess.CompletedProcess[str]) -> str:
    """Last stdout line — `git rev-parse --show-prefix` prints '' at the root."""
    lines = res.stdout.strip().splitlines()
    return lines[-1] if lines else ""
