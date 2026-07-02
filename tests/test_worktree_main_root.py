"""Unit tests for ``WorktreeManager.main_worktree_root`` (HATS-524).

The CLI fix (``cli/_helpers.py::_project_dir``) relies on this helper to
hop from a linked git worktree back to the main checkout. These tests
pin the helper's contract directly against real git, mirroring the
subprocess-git pattern used by ``test_worktree.py`` for
``is_inside_linked_worktree``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_hats_wt import WorktreeManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "t@t.co")
    _git(path, "config", "user.name", "t")
    _git(path, "commit", "--allow-empty", "-m", "init")


def test_main_worktree_root_none_in_main_repo(tmp_path: Path) -> None:
    # Main worktree: --git-dir == --git-common-dir → no hop, returns None.
    proj = tmp_path / "repo"
    _init_repo(proj)
    assert WorktreeManager.main_worktree_root(proj) is None


def test_main_worktree_root_none_for_non_git(tmp_path: Path) -> None:
    # Non-git path → None (fail-safe).
    assert WorktreeManager.main_worktree_root(tmp_path) is None


def test_main_worktree_root_resolves_from_linked_worktree(tmp_path: Path) -> None:
    # Linked worktree, deliberately OUTSIDE the main tree, resolves back to
    # the main checkout root.
    proj = tmp_path / "repo"
    _init_repo(proj)
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))
    assert (wt / ".git").is_file()  # linked worktree → .git is a pointer file

    resolved = WorktreeManager.main_worktree_root(wt)
    assert resolved is not None
    assert resolved.resolve() == proj.resolve()
