"""e2e (HATS-524, HATS-1263)

flow:   a developer executing rack commands from inside a linked worktree directory
cmds:
    # from inside a linked worktree directory
    rack context HATS-1
expect: rack resolves the main repository tracker directory and successfully reads or
        updates task card data
why:    rack commands issued inside linked worktrees must locate the main repository
        tracker without requiring relative path navigation
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _rack(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The backlog CLI (HATS-1263). This tier ships no ``rack`` console script,
    but the dev venv has the package, so ``python -m`` reaches it."""
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _task_dir(root: Path, task_id: str) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id


def test_task_ops_from_worktree_route_to_main_tracker(tmp_project, tmp_path):
    main = tmp_project

    # The tracker is gitignored in production — reproduce that so the
    # worktree checkout does NOT carry a snapshot of .agent/.
    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init")
    _git(main.path, "config", "user.email", "test@example.com")
    _git(main.path, "config", "user.name", "Test")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # Task created in the MAIN checkout's live tracker.
    created = _rack("create", "Worktree task", "--id", "HATS-1", cwd=main.path)
    assert created.returncode == 0, created.stderr
    assert _task_dir(main.path, "HATS-1").is_dir()

    # Linked worktree, deliberately OUTSIDE the main tree (sibling under
    # tmp_path) so `main` is not a filesystem ancestor.
    wt = tmp_path / "linked-wt"
    _git(main.path, "worktree", "add", "-b", "wt-branch", str(wt))
    assert (wt / ".git").is_file()  # linked worktree → .git is a pointer file
    assert not (wt / ".agent").exists()  # gitignored → absent in the worktree

    # `rack context` from the worktree cwd must resolve the main tracker.
    shown = _rack("context", "HATS-1", cwd=wt)
    assert shown.returncode == 0, (
        f"rack context from worktree must succeed (HATS-524)\n"
        f"stdout:\n{shown.stdout}\nstderr:\n{shown.stderr}"
    )
    assert "Worktree task" in shown.stdout, shown.stdout

    # A work_log note from the worktree cwd must land in the MAIN tracker, not
    # a stray `<worktree>/.agent/`. `--log` is rack's `task log`.
    logged = _rack("transition", "HATS-1", "--log", "note from worktree", cwd=wt)
    assert logged.returncode == 0, logged.stderr
    assert not (wt / ".agent").exists(), "log must not create a tracker in the worktree"

    shown2 = _rack("context", "HATS-1", cwd=main.path)
    assert "note from worktree" in shown2.stdout, shown2.stdout
