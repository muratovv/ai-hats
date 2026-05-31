"""e2e: `ai-hats task` ops from inside a linked git worktree route to the
main checkout's tracker (HATS-524).

Repro of the original bug: the tracker (`.agent/`) is gitignored and
`ai-hats.yaml` is untracked, so a linked worktree's checkout carries
neither. The worktree also lives OUTSIDE the main tree, so walking up
from the worktree's cwd never reaches the main checkout. Before the fix
`_project_dir` stopped at the worktree's own `.git` *file* and resolved
the tracker to a non-existent `<worktree>/.agent/` → "Task <ID> not found".

The fix hops from a `.git`-file (linked worktree) to the main worktree
root via git's commondir, so task ops issued from the worktree cwd act
on the one live tracker in the main checkout.

Fail-under-revert: with the old `_project_dir`, `task show` from the
worktree exits non-zero.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _ai_hats(binary: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
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
    binary = main.ai_hats_binary

    # The tracker is gitignored in production — reproduce that so the
    # worktree checkout does NOT carry a snapshot of .agent/.
    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init")
    _git(main.path, "config", "user.email", "test@example.com")
    _git(main.path, "config", "user.name", "Test")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # Task created in the MAIN checkout's live tracker.
    created = _ai_hats(binary, "task", "create", "Worktree task", "--id", "HATS-1",
                       cwd=main.path)
    assert created.returncode == 0, created.stderr
    assert _task_dir(main.path, "HATS-1").is_dir()

    # Linked worktree, deliberately OUTSIDE the main tree (sibling under
    # tmp_path) so `main` is not a filesystem ancestor.
    wt = tmp_path / "linked-wt"
    _git(main.path, "worktree", "add", "-b", "wt-branch", str(wt))
    assert (wt / ".git").is_file()  # linked worktree → .git is a pointer file
    assert not (wt / ".agent").exists()  # gitignored → absent in the worktree

    # `task show` from the worktree cwd must resolve the main tracker.
    shown = _ai_hats(binary, "task", "show", "HATS-1", cwd=wt)
    assert shown.returncode == 0, (
        f"task show from worktree must succeed (HATS-524)\n"
        f"stdout:\n{shown.stdout}\nstderr:\n{shown.stderr}"
    )
    assert "Worktree task" in shown.stdout, shown.stdout

    # `task log` from the worktree cwd must land in the MAIN tracker, not a
    # stray `<worktree>/.agent/`.
    logged = _ai_hats(binary, "task", "log", "HATS-1", "note from worktree", cwd=wt)
    assert logged.returncode == 0, logged.stderr
    assert not (wt / ".agent").exists(), "log must not create a tracker in the worktree"

    shown2 = _ai_hats(binary, "task", "show", "HATS-1", cwd=main.path)
    assert "note from worktree" in shown2.stdout, shown2.stdout
