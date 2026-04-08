"""Tests for agent-driven worktree flow: persistent create/merge/discard/list."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_hats.worktree import WorktreeManager


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test")
    (project / ".agent").mkdir()
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


# ---------------------------------------------------------------------------
# Explicit branch name
# ---------------------------------------------------------------------------

class TestExplicitBranch:
    def test_create_with_branch_name(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/my-feature")
        wt = mgr.create()
        try:
            branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            assert branch == "feat/my-feature"
        finally:
            mgr.cleanup()

    def test_branch_name_overrides_role_session(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "role", "sess", branch_name="custom/branch")
        wt = mgr.create()
        try:
            branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            assert branch == "custom/branch"
        finally:
            mgr.cleanup()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_save_state_creates_file(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/test-save")
        mgr.create()
        state_path = mgr.save_state()
        try:
            assert state_path.exists()
            data = json.loads(state_path.read_text())
            assert data["branch"] == "feat/test-save"
            assert Path(data["worktree_path"]).is_dir()
            assert data["original_branch"]
        finally:
            mgr.cleanup()

    def test_load_active_restores_state(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/test-load")
        wt = mgr.create()
        mgr.save_state()

        # Simulate new process — load from state
        loaded = WorktreeManager.load_active(git_project)
        try:
            assert loaded is not None
            assert loaded.branch_name == "feat/test-load"
            assert loaded.worktree_path == wt
            assert loaded.worktree_path.is_dir()
        finally:
            loaded.cleanup()

    def test_load_active_returns_none_when_no_state(self, git_project: Path) -> None:
        assert WorktreeManager.load_active(git_project) is None

    def test_load_active_returns_none_when_stale(self, git_project: Path) -> None:
        """If worktree dir was deleted externally, load_active cleans up."""
        state_path = git_project / ".agent" / "worktree.json"
        state_path.write_text(json.dumps({
            "branch": "feat/stale",
            "worktree_path": "/tmp/nonexistent-worktree-12345",
            "original_branch": "master",
        }))
        assert WorktreeManager.load_active(git_project) is None
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Persistent merge flow (create → save → load → merge)
# ---------------------------------------------------------------------------

class TestPersistentMerge:
    def test_create_save_load_merge(self, git_project: Path) -> None:
        """Full agent flow: create → work → save → load → merge."""
        # Agent starts task
        mgr = WorktreeManager(git_project, branch_name="feat/agent-task")
        wt = mgr.create()
        mgr.save_state()

        # Agent works in worktree
        (wt / "result.txt").write_text("done")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "complete task")

        # Later: agent merges
        mgr2 = WorktreeManager.load_active(git_project)
        assert mgr2 is not None
        mgr2.merge(squash=True)

        # Result is in main tree
        assert (git_project / "result.txt").read_text() == "done"
        # State file is gone
        assert not (git_project / ".agent" / "worktree.json").exists()
        # Worktree dir is gone
        assert not wt.exists()

    def test_create_save_load_discard(self, git_project: Path) -> None:
        """Agent decides to discard work."""
        mgr = WorktreeManager(git_project, branch_name="feat/bad-idea")
        wt = mgr.create()
        mgr.save_state()

        (wt / "junk.txt").write_text("nope")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "bad commit")

        mgr2 = WorktreeManager.load_active(git_project)
        assert mgr2 is not None
        mgr2.discard()

        assert not (git_project / "junk.txt").exists()
        assert not wt.exists()
        assert not (git_project / ".agent" / "worktree.json").exists()

    def test_merge_no_squash(self, git_project: Path) -> None:
        """Regular merge (not squash)."""
        mgr = WorktreeManager(git_project, branch_name="feat/full-merge")
        wt = mgr.create()
        mgr.save_state()

        (wt / "merged.txt").write_text("merged")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "a change")

        mgr2 = WorktreeManager.load_active(git_project)
        mgr2.merge(squash=False)

        assert (git_project / "merged.txt").read_text() == "merged"


# ---------------------------------------------------------------------------
# List worktrees
# ---------------------------------------------------------------------------

class TestListWorktrees:
    def test_list_includes_created_worktree(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/listed")
        mgr.create()
        try:
            wts = WorktreeManager.list_worktrees(git_project)
            branches = [w.get("branch", "") for w in wts]
            assert "feat/listed" in branches
        finally:
            mgr.cleanup()

    def test_list_on_plain_dir(self, tmp_path: Path) -> None:
        assert WorktreeManager.list_worktrees(tmp_path) == []

    def test_list_shows_main_worktree(self, git_project: Path) -> None:
        wts = WorktreeManager.list_worktrees(git_project)
        assert len(wts) >= 1
        paths = [w["path"] for w in wts]
        assert str(git_project) in paths


# ---------------------------------------------------------------------------
# is_inside_linked_worktree (HATS-060)
# ---------------------------------------------------------------------------


class TestIsInsideLinkedWorktree:
    def test_main_worktree_returns_false(self, git_project: Path) -> None:
        assert WorktreeManager.is_inside_linked_worktree(git_project) is False

    def test_linked_worktree_returns_true(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/linked")
        wt = mgr.create()
        try:
            assert WorktreeManager.is_inside_linked_worktree(wt) is True
        finally:
            mgr.cleanup()

    def test_nested_subdir_of_linked_worktree_returns_true(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/nested")
        wt = mgr.create()
        try:
            sub = wt / "a" / "b"
            sub.mkdir(parents=True)
            assert WorktreeManager.is_inside_linked_worktree(sub) is True
        finally:
            mgr.cleanup()

    def test_non_git_dir_returns_false(self, tmp_path: Path) -> None:
        assert WorktreeManager.is_inside_linked_worktree(tmp_path) is False
