"""e2e (HATS-859): ``ai-hats wt exec <branch> -- <cmd…>`` routes to the named
worktree when >1 active. Fail-under-revert: the ``_resolve_worktree()`` no-arg
call swallows the selector into ``cmd_args`` → ambiguity ``UsageError``.

Subprocess env targets the checkout under test (``repo_root``): PYTHONPATH +
AI_HATS_LIBRARY_ROOT (HATS-826) exercises worktree code — plain PYTHONPATH=src
alone hits the HATS-685 vanished-roles trap.
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
    env["AI_HATS_LIBRARY_ROOT"] = str(repo_root / "library")
    return env


def _ai_hats(binary: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
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
    assert _ai_hats(binary, "task", "create", task_id, "--id", task_id, cwd=main, env=env).returncode == 0
    assert _ai_hats(binary, "task", "transition", task_id, "plan", cwd=main, env=env).returncode == 0
    (_tracker(main) / task_id / "plan.md").write_text(_PLAN)
    r = _ai_hats(binary, "task", "transition", task_id, "execute", cwd=main, env=env)
    assert r.returncode == 0, r.stderr


def test_wt_exec_selector_routes_to_named_worktree(tmp_project, repo_root):
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    # Two managed worktrees → _resolve_worktree() with no selector is ambiguous.
    _spawn_worktree(binary, main.path, "HATS-1", env)
    _spawn_worktree(binary, main.path, "HATS-2", env)
    branches = _worktree_branches(main.path)
    assert len(branches) >= 2, f"expected two linked worktrees, got {branches}"
    picks = sorted(branches)[:2]

    # A leading selector that names an active worktree must route there — both
    # with and without the `--` separator. The inner `git rev-parse` reports the
    # branch of whatever worktree it actually ran in.
    for branch in picks:
        with_dd = _ai_hats(
            binary, "wt", "exec", branch, "--", "git", "rev-parse", "--abbrev-ref", "HEAD",
            cwd=main.path, env=env,
        )
        assert with_dd.returncode == 0, (
            f"🐛 HATS-859 REGRESSION: `wt exec {branch} -- …` failed instead of routing:\n"
            f"stdout:\n{with_dd.stdout}\nstderr:\n{with_dd.stderr}"
        )
        assert with_dd.stdout.strip().splitlines()[-1] == branch, (
            f"selector `{branch}` ran in the wrong worktree: got {with_dd.stdout!r}"
        )

        no_dd = _ai_hats(
            binary, "wt", "exec", branch, "git", "rev-parse", "--abbrev-ref", "HEAD",
            cwd=main.path, env=env,
        )
        assert no_dd.returncode == 0 and no_dd.stdout.strip().splitlines()[-1] == branch, (
            f"selector `{branch}` without `--` mis-routed: rc={no_dd.returncode} "
            f"stdout={no_dd.stdout!r} stderr={no_dd.stderr!r}"
        )


def test_wt_exec_without_selector_still_reports_ambiguity(tmp_project, repo_root):
    """R2 contract: with >1 worktree and no selector, the command must still
    refuse with the actionable ambiguity error (not silently pick one)."""
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    _init_repo(main.path)
    _spawn_worktree(binary, main.path, "HATS-1", env)
    _spawn_worktree(binary, main.path, "HATS-2", env)

    res = _ai_hats(
        binary, "wt", "exec", "--", "git", "rev-parse", "--abbrev-ref", "HEAD",
        cwd=main.path, env=env,
    )
    assert res.returncode != 0, "no-selector form must refuse when >1 worktree active"
    assert "Multiple active worktrees" in (res.stderr + res.stdout)
