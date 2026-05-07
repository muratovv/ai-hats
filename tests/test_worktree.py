"""Tests for git worktree isolation (HATS-004)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.worktree import (
    IsolationMode,
    OriginalBranchMissingError,
    WorktreeManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


# ---------------------------------------------------------------------------
# IsolationMode
# ---------------------------------------------------------------------------

class TestIsolationMode:
    def test_values(self) -> None:
        assert IsolationMode.DISCARD == "discard"
        assert IsolationMode.SQUASH == "squash"
        assert IsolationMode.BRANCH == "branch"
        assert IsolationMode.NONE == "none"

    def test_from_string(self) -> None:
        assert IsolationMode("discard") is IsolationMode.DISCARD
        assert IsolationMode("squash") is IsolationMode.SQUASH
        assert IsolationMode("branch") is IsolationMode.BRANCH
        assert IsolationMode("none") is IsolationMode.NONE

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            IsolationMode("unknown")


class TestIsolationModeNone:
    """NONE mode skips git worktree creation — sub-agent runs in project_dir."""

    def test_create_returns_project_dir(self, git_project: Path) -> None:
        mgr = WorktreeManager(
            git_project, "tester", "sess-none-001", IsolationMode.NONE,
        )
        wt = mgr.create()
        assert wt.resolve() == git_project.resolve()
        assert mgr.worktree_path is None

    def test_context_manager_no_op_cleanup(self, git_project: Path) -> None:
        mgr = WorktreeManager(
            git_project, "tester", "sess-none-002", IsolationMode.NONE,
        )
        with mgr as wt:
            assert wt.resolve() == git_project.resolve()
        # No exception, no worktree created, no cleanup needed
        assert mgr.worktree_path is None

    def test_no_branch_created(self, git_project: Path) -> None:
        """Verify no `agent/...` branch is created in NONE mode."""
        mgr = WorktreeManager(
            git_project, "tester", "sess-none-003", IsolationMode.NONE,
        )
        with mgr:
            pass
        result = _git(git_project, "branch", "--list", "agent/tester/sess-none-003")
        assert result.stdout.strip() == "", \
            "NONE mode must not create a branch"

    def test_works_in_non_git_dir(self, tmp_path: Path) -> None:
        """NONE mode must work even outside a git repo (no _check_is_git call)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        mgr = WorktreeManager(
            proj, "tester", "sess-none-004", IsolationMode.NONE,
        )
        with mgr as wt:
            assert wt.resolve() == proj.resolve()


# ---------------------------------------------------------------------------
# WorktreeManager — create / cleanup
# ---------------------------------------------------------------------------

class TestWorktreeCreate:
    def test_create_returns_different_path(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-001")
        wt = mgr.create()
        try:
            assert wt != git_project
            assert wt.is_dir()
        finally:
            mgr.cleanup()

    def test_worktree_is_valid_git_repo(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-002")
        wt = mgr.create()
        try:
            result = _git(wt, "status")
            assert result.returncode == 0
        finally:
            mgr.cleanup()

    def test_worktree_branch_name(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "coder", "sess-003")
        wt = mgr.create()
        try:
            result = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            assert result.stdout.strip() == "agent/coder/sess-003"
        finally:
            mgr.cleanup()

    def test_worktree_has_same_content(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-004")
        wt = mgr.create()
        try:
            assert (wt / "README.md").read_text() == "# Test"
        finally:
            mgr.cleanup()

    def test_create_raises_on_unborn_head(self, tmp_path: Path) -> None:
        """HATS-143: fresh `git init` without commits must raise a readable error."""
        project = tmp_path / "empty"
        project.mkdir()
        _git(project, "init")
        mgr = WorktreeManager(project, "tester", "sess-unborn")
        with pytest.raises(RuntimeError, match="at least one commit"):
            mgr.create()


class TestWorktreeIsolation:
    def test_changes_not_visible_in_main(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-010")
        wt = mgr.create()
        try:
            (wt / "new_file.txt").write_text("from worktree")
            assert not (git_project / "new_file.txt").exists()
        finally:
            mgr.cleanup()


class TestWorktreeCleanup:
    def test_cleanup_removes_dir(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-020")
        wt = mgr.create()
        assert wt.is_dir()
        mgr.cleanup()
        assert not wt.exists()

    def test_discard_deletes_branch(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-021", IsolationMode.DISCARD)
        mgr.create()
        mgr.cleanup()
        result = subprocess.run(
            ["git", "branch", "--list", "agent/tester/sess-021"],
            cwd=str(git_project),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""

    def test_squash_merges_changes(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-022", IsolationMode.SQUASH)
        wt = mgr.create()
        # Make a change in worktree and commit it
        (wt / "feature.txt").write_text("new feature")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "add feature")
        mgr.cleanup()
        # File should now be in main tree
        assert (git_project / "feature.txt").exists()
        assert (git_project / "feature.txt").read_text() == "new feature"

    def test_squash_no_changes_is_noop(self, git_project: Path) -> None:
        """Squash with no commits in worktree should not create a merge commit."""
        head_before = _git(git_project, "rev-parse", "HEAD").stdout.strip()
        mgr = WorktreeManager(git_project, "tester", "sess-023", IsolationMode.SQUASH)
        mgr.create()
        mgr.cleanup()
        head_after = _git(git_project, "rev-parse", "HEAD").stdout.strip()
        assert head_before == head_after

    def test_branch_keeps_branch(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, "tester", "sess-024", IsolationMode.BRANCH)
        wt = mgr.create()
        (wt / "kept.txt").write_text("kept")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "keep this")
        mgr.cleanup()
        # Dir removed but branch still exists
        assert not wt.exists()
        result = subprocess.run(
            ["git", "branch", "--list", "agent/tester/sess-024"],
            cwd=str(git_project),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() != ""


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestContextManager:
    def test_enter_returns_path_exit_cleans(self, git_project: Path) -> None:
        with WorktreeManager(git_project, "tester", "sess-030") as wt:
            assert wt.is_dir()
            assert wt != git_project
            wt_path = wt
        assert not wt_path.exists()

    def test_exception_forces_discard(self, git_project: Path) -> None:
        """On exception, cleanup should happen and branch should be deleted (discard)."""
        wt_path: Path | None = None
        with pytest.raises(RuntimeError):
            with WorktreeManager(git_project, "tester", "sess-031", IsolationMode.SQUASH) as wt:
                wt_path = wt
                (wt / "crash.txt").write_text("boom")
                _git(wt, "add", ".")
                _git(wt, "commit", "-m", "crash commit")
                raise RuntimeError("boom")
        assert wt_path is not None
        assert not wt_path.exists()
        # Changes should NOT have been merged (forced discard)
        assert not (git_project / "crash.txt").exists()


# ---------------------------------------------------------------------------
# Original branch missing (HATS-253)
# ---------------------------------------------------------------------------

class TestOriginalBranchMissing:
    def test_merge_raises_when_original_branch_deleted(self, git_project: Path) -> None:
        """If the original branch was deleted while worktree was active,
        merge() must raise OriginalBranchMissingError, remove the worktree
        directory, but preserve the worktree branch for manual recovery."""
        # Create another branch and switch to it so we can delete the
        # branch the worktree was created from.
        _git(git_project, "checkout", "-b", "doomed")
        mgr = WorktreeManager(git_project, "tester", "sess-253", IsolationMode.SQUASH)
        wt = mgr.create()
        (wt / "feature.txt").write_text("wip")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "wip")
        # Move main repo off the doomed branch and delete it
        _git(git_project, "checkout", "-")  # back to master/main
        _git(git_project, "branch", "-D", "doomed")

        with pytest.raises(OriginalBranchMissingError):
            mgr.merge()

        # Worktree dir gone, but worktree branch preserved
        assert not wt.exists()
        result = subprocess.run(
            ["git", "branch", "--list", mgr.branch_name],
            cwd=str(git_project),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() != "", "worktree branch should be preserved"


# ---------------------------------------------------------------------------
# Non-git fallback
# ---------------------------------------------------------------------------

class TestNonGitFallback:
    def test_non_git_returns_project_dir(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        mgr = WorktreeManager(plain_dir, "tester", "sess-040")
        wt = mgr.create()
        assert wt == plain_dir
        mgr.cleanup()  # should be a noop

    def test_non_git_context_manager(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        with WorktreeManager(plain_dir, "tester", "sess-041") as wt:
            assert wt == plain_dir


# ---------------------------------------------------------------------------
# Parallel
# ---------------------------------------------------------------------------

class TestParallel:
    def test_two_managers_no_conflict(self, git_project: Path) -> None:
        mgr1 = WorktreeManager(git_project, "role-a", "sess-050")
        mgr2 = WorktreeManager(git_project, "role-b", "sess-051")
        wt1 = mgr1.create()
        wt2 = mgr2.create()
        try:
            assert wt1 != wt2
            # Both are valid
            assert (wt1 / "README.md").exists()
            assert (wt2 / "README.md").exists()
            # Changes in one don't affect the other
            (wt1 / "from_a.txt").write_text("a")
            assert not (wt2 / "from_a.txt").exists()
        finally:
            mgr1.cleanup()
            mgr2.cleanup()
