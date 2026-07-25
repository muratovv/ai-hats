"""e2e (HATS-1205): ``ai-hats wt exec`` runs the command WHERE YOU STAND — an
environment wrapper, not a teleporter.

Fail-under-revert: restore the unconditional ``cwd=str(wt_path)`` in ``wt_exec``
and ``git rev-parse --show-prefix`` reports ``""`` (the root), not ``sub/``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_PLAN = """# Plan
## Requirements
do the thing
## Scope & Out-of-scope
in: thing; out: other
## Steps
1. thing
## Verification Protocol
run it
"""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


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


def _tracker(root: Path) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"


def _worktree_branches(main: Path) -> dict[str, Path]:
    """Map branch-name -> path for every linked ai-hats worktree."""
    out = _git(main, "worktree", "list", "--porcelain").stdout
    branches: dict[str, Path] = {}
    cur_path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            p = Path(line[len("worktree ") :])
            cur_path = p if "ai-hats-wt" in line else None
        elif line.startswith("branch ") and cur_path is not None:
            name = line[len("branch ") :].removeprefix("refs/heads/")
            branches[name] = cur_path
    return branches


def _init_repo(main: Path) -> None:
    (main / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@e")
    _git(main, "config", "user.name", "T")
    _git(main, "add", "-A")
    _git(main, "commit", "-m", "init", "--allow-empty")


def _spawn_worktree(binary: Path, main: Path, task_id: str, env: dict[str, str]) -> None:
    assert (
        _ai_hats(binary, "task", "create", task_id, "--id", task_id, cwd=main, env=env).returncode
        == 0
    )
    assert _ai_hats(binary, "task", "transition", task_id, "plan", cwd=main, env=env).returncode == 0
    (_tracker(main) / task_id / "plan.md").write_text(_PLAN)
    r = _ai_hats(binary, "task", "transition", task_id, "execute", cwd=main, env=env)
    assert r.returncode == 0, r.stderr


def _prefix(res: subprocess.CompletedProcess[str]) -> str:
    """Last line of stdout — `git rev-parse --show-prefix` prints '' at the root."""
    lines = res.stdout.strip().splitlines()
    return lines[-1] if lines else ""


def test_wt_exec_runs_in_the_subdirectory_you_stand_in(tmp_project, repo_root):
    """S1: inside `<wt>/sub`, the bare form needs no selector and no flag."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    # Two worktrees: cwd must do real work — the sole-worktree convenience path
    # would otherwise resolve the branch and mask what is under test.
    _spawn_worktree(binary, main.path, "HATS-1", env)
    _spawn_worktree(binary, main.path, "HATS-2", env)
    branches = _worktree_branches(main.path)
    assert len(branches) >= 2, f"expected two linked worktrees, got {branches}"

    branch = sorted(branches)[0]
    wt = branches[branch]
    sub = wt / "sub"
    sub.mkdir()

    res = _ai_hats(binary, "wt", "exec", "--", "git", "rev-parse", "--show-prefix", cwd=sub, env=env)

    assert res.returncode == 0, (
        "🐛 HATS-1205: the bare `wt exec` form must resolve from cwd inside a "
        f"worktree subdirectory:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert _prefix(res) == "sub/", (
        "🐛 HATS-1205: `wt exec` teleported to the worktree root instead of "
        f"running where the caller stands. Expected 'sub/', got {res.stdout!r}"
    )


def test_wt_exec_from_outside_still_lands_at_the_worktree_root(tmp_project, repo_root):
    """R5: invocations from the main checkout are unchanged — every published
    example is of this shape, so this is the back-compat guard."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(binary, main.path, "HATS-1", env)
    _spawn_worktree(binary, main.path, "HATS-2", env)
    branches = _worktree_branches(main.path)
    branch = sorted(branches)[0]
    (branches[branch] / "sub").mkdir()

    res = _ai_hats(
        binary, "wt", "exec", branch, "--", "git", "rev-parse", "--show-prefix",
        cwd=main.path, env=env,
    )

    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert _prefix(res) == "", (
        "from outside the worktree the command must still start at the worktree "
        f"root, got prefix {_prefix(res)!r}"
    )
