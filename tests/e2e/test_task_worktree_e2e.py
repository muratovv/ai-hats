"""e2e: `rack` task ops from inside a linked git worktree route to the
main checkout's tracker (HATS-524; re-pointed off `ai-hats task`, HATS-1263).

Repro of the original bug: the tracker (`.agent/`) is gitignored and
`ai-hats.yaml` is untracked, so a linked worktree's checkout carries
neither. The worktree also lives OUTSIDE the main tree, so walking up
from the worktree's cwd never reaches the main checkout. Before the fix
resolution stopped at the worktree's own `.git` *file* and resolved
the tracker to a non-existent `<worktree>/.agent/` → "Task <ID> not found".

The fix hops from a `.git`-file (linked worktree) to the main worktree
root via git's commondir (`ai_hats_rack/resolver.py::_main_worktree_root`),
so task ops issued from the worktree cwd act on the one live tracker.

Fail-under-revert: without the hop, `rack context` from the worktree
exits non-zero.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


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
