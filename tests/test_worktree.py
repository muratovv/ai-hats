"""Tests for git worktree isolation (HATS-004)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.worktree import (
    IsolationMode,
    OriginalBranchMissingError,
    WorktreeBaseBranchMismatchError,
    WorktreeCreateError,
    WorktreeDirtyError,
    WorktreeManager,
    WorktreeStateIncompleteError,
)


pytestmark = pytest.mark.integration


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


class TestEmptyRoleGuard:
    """HATS-827: an empty role must never silently yield an invalid branch
    like ``agent//<sid>`` — the constructor raises early instead."""

    def test_empty_role_raises(self, git_project: Path) -> None:
        with pytest.raises(ValueError, match="empty role segment"):
            WorktreeManager(git_project, role_name="", session_id="sess-040")

    def test_nonempty_role_still_builds_branch(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, role_name="coder", session_id="sess-041")
        assert mgr.branch_name == "agent/coder/sess-041"

    def test_explicit_branch_name_bypasses_role_guard(self, git_project: Path) -> None:
        # Persistent CLI path supplies branch_name directly; an empty role
        # segment is irrelevant and must not trip the guard.
        mgr = WorktreeManager(git_project, branch_name="task/hats-827")
        assert mgr.branch_name == "task/hats-827"


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

    def test_merge_raises_when_original_branch_is_none(self, git_project: Path) -> None:
        """HATS-714: a state file present but with ``original_branch=None``
        (corrupt / hand-edited / pre-versioned legacy) must raise the typed
        ``WorktreeStateIncompleteError`` — not the pre-714 opaque ``TypeError``
        from ``git rev-parse None`` — and must refuse before any mutation, so
        the worktree dir and branch are preserved."""
        mgr = WorktreeManager(git_project, "tester", "sess-714", IsolationMode.SQUASH)
        wt = mgr.create()
        mgr.save_state()
        # Simulate the corrupt / legacy field the way _load_by_key would
        # surface it: original_branch resolves to None.
        mgr._original_branch = None

        with pytest.raises(WorktreeStateIncompleteError):
            mgr.merge()

        # Refusal precedes mutation: worktree dir + branch both intact.
        assert wt.exists(), "worktree dir must survive a pre-mutation refusal"
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


# ---------------------------------------------------------------------------
# HATS-517 — branch-exists classifier (Case A / B / C)
# ---------------------------------------------------------------------------

class TestBranchExistsClassifier:
    """`WorktreeManager.create()` must handle the three sub-cases where
    the target branch already exists, instead of failing the original
    happy path with "branch already exists".

    Case A — branch exists, no worktree owns it → attach to a new linked
             worktree (positional `git worktree add <path> <branch>`).
    Case B — branch is checked out in the MAIN worktree (project_dir) →
             refuse with actionable hint.
    Case C — branch is already a LINKED worktree, state JSON missing →
             adopt the existing path, persist a fresh state.
    """

    def test_case_a_attach_existing_branch(self, git_project: Path) -> None:
        """Case A: `git branch task/foo` then create() must attach, not fail.

        State persistence is the caller's responsibility (mirrors the
        original happy path — `_setup_worktree` calls `save_state()` after
        `create()`); we only assert that the worktree exists on the
        pre-existing branch.
        """
        _git(git_project, "branch", "task/foo")
        mgr = WorktreeManager(git_project, branch_name="task/foo")
        wt = mgr.create()
        try:
            # Worktree on the pre-existing branch.
            result = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            assert result.stdout.strip() == "task/foo"
            assert wt != git_project
            assert wt.is_dir()
        finally:
            mgr.cleanup()

    def test_case_a_lifecycle_merge_works(self, git_project: Path) -> None:
        """Case A: full lifecycle — attach, commit, merge back into master."""
        _git(git_project, "branch", "task/bar")
        mgr = WorktreeManager(git_project, branch_name="task/bar")
        wt = mgr.create()
        # Make a commit in the worktree.
        (wt / "feature.txt").write_text("hello")
        _git(wt, "add", "feature.txt")
        _git(wt, "-c", "user.email=t@t.com", "-c", "user.name=T",
             "commit", "-m", "feat")
        # Merge — must succeed (Case A lifecycle parity with happy path).
        mgr.merge()
        # File is now on master, branch is gone, worktree dir is gone.
        assert (git_project / "feature.txt").read_text() == "hello"
        result = _git(git_project, "branch", "--list", "task/bar")
        assert result.stdout.strip() == ""

    def test_case_a_failure_does_not_delete_user_branch(
        self, git_project: Path, monkeypatch
    ) -> None:
        """L4 rollback must NOT delete a pre-existing user branch on failure."""
        _git(git_project, "branch", "task/keep")
        mgr = WorktreeManager(git_project, branch_name="task/keep")

        # Force `git worktree add` to fail (non-retriable error).
        import ai_hats.worktree as wtmod
        real_retry = wtmod._retry_worktree_add

        def boom(*args, **kwargs):
            raise subprocess.CalledProcessError(
                1, ["git", "worktree", "add"], stderr="fatal: boom\n"
            )

        monkeypatch.setattr(wtmod, "_retry_worktree_add", boom)
        with pytest.raises(WorktreeCreateError):
            mgr.create()
        monkeypatch.setattr(wtmod, "_retry_worktree_add", real_retry)

        # Pre-existing branch must still be there.
        result = _git(git_project, "branch", "--list", "task/keep")
        assert "task/keep" in result.stdout

    def test_case_b_refuse_when_checked_out_in_main(
        self, git_project: Path
    ) -> None:
        """Case B: branch is currently HEAD of main worktree → refuse."""
        _git(git_project, "checkout", "-b", "task/main-collision")
        mgr = WorktreeManager(git_project, branch_name="task/main-collision")
        with pytest.raises(WorktreeCreateError) as exc_info:
            mgr.create()
        msg = str(exc_info.value)
        # Hint content — both actionable alternatives surfaced.
        assert "checked out in the main worktree" in msg
        assert "git switch" in msg
        assert "task close" in msg

    def test_case_c_adopt_orphaned_linked_worktree(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """Case C: linked worktree exists, state JSON missing → adopt + restore."""
        # Create a linked worktree the manual way.
        linked_path = tmp_path / "manual-linked"
        _git(git_project, "worktree", "add", "-b", "task/orphan", str(linked_path))
        # No state JSON exists for it (we never went through ai-hats).
        from ai_hats.worktree import _state_key
        from ai_hats.paths import worktrees_dir
        state = worktrees_dir(git_project) / f"{_state_key('task/orphan')}.json"
        assert not state.exists()

        # Now call create() — should adopt, not fail.
        mgr = WorktreeManager(git_project, branch_name="task/orphan")
        wt = mgr.create()
        assert wt.resolve() == linked_path.resolve()
        # State JSON re-created.
        assert state.exists()

    def test_case_c_refuse_when_directory_missing(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """Case C subtlety: linked admin entry exists but dir is rmtree'd."""
        import shutil
        linked_path = tmp_path / "ghost"
        _git(git_project, "worktree", "add", "-b", "task/ghost", str(linked_path))
        # Nuke the dir without `git worktree remove` — orphan admin entry.
        shutil.rmtree(linked_path)

        mgr = WorktreeManager(git_project, branch_name="task/ghost")
        with pytest.raises(WorktreeCreateError) as exc_info:
            mgr.create()
        msg = str(exc_info.value)
        assert "git worktree prune" in msg

    def test_happy_path_unchanged(self, git_project: Path) -> None:
        """Branch does NOT exist → classifier must fall through unchanged."""
        mgr = WorktreeManager(git_project, branch_name="task/fresh")
        wt = mgr.create()
        try:
            assert wt != git_project
            result = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            assert result.stdout.strip() == "task/fresh"
        finally:
            mgr.cleanup()


# ---------------------------------------------------------------------------
# Already-merged short-circuit (HATS-596)
# ---------------------------------------------------------------------------

class TestAlreadyMergedShortCircuit:
    """HATS-596: ``merge()`` is checkout-independent when the branch is
    already an ancestor of the recorded base — no ``git merge`` runs, so the
    main-repo HEAD position (wandered or not) is irrelevant; the worktree is
    torn down cleanly instead of refusing with
    :class:`WorktreeBaseBranchMismatchError`."""

    @staticmethod
    def _setup_already_merged(git_project: Path, sess: str):
        """Create a worktree, commit work, merge the branch into base, then
        wander the main-repo HEAD to a foreign branch.

        Returns ``(mgr, wt, base_branch, base_sha_after_merge)``.
        """
        base_branch = _git(
            git_project, "rev-parse", "--abbrev-ref", "HEAD"
        ).stdout.strip()
        mgr = WorktreeManager(git_project, "tester", sess)
        wt = mgr.create()
        (wt / "feature.txt").write_text("wip")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "wip")
        # Integrate the branch into base (as if already merged + pushed).
        _git(git_project, "merge", "--no-ff", "--no-edit", mgr.branch_name)
        base_sha = _git(git_project, "rev-parse", base_branch).stdout.strip()
        # Main-repo HEAD wanders to a foreign branch.
        _git(git_project, "checkout", "-b", "wandered")
        return mgr, wt, base_branch, base_sha

    def test_already_merged_wandered_head_tears_down(
        self, git_project: Path
    ) -> None:
        mgr, wt, base_branch, base_sha = self._setup_already_merged(
            git_project, "sess-596a"
        )
        # No exception despite main-repo HEAD on `wandered` (not base).
        mgr.merge()
        assert not wt.exists(), "worktree dir should be removed"
        branches = _git(
            git_project, "branch", "--list", mgr.branch_name
        ).stdout.strip()
        assert branches == "", "task branch should be deleted"
        # No double-merge: base ref untouched.
        assert _git(
            git_project, "rev-parse", base_branch
        ).stdout.strip() == base_sha
        # Main-repo HEAD untouched.
        assert _git(
            git_project, "rev-parse", "--abbrev-ref", "HEAD"
        ).stdout.strip() == "wandered"

    def test_already_merged_dirty_no_force_raises_dirty(
        self, git_project: Path
    ) -> None:
        mgr, wt, _base, _sha = self._setup_already_merged(
            git_project, "sess-596b"
        )
        # Uncommitted edit in the worktree → short-circuit honors _check_clean.
        (wt / "feature.txt").write_text("uncommitted change")
        with pytest.raises(WorktreeDirtyError):
            mgr.merge()
        # Worktree + branch preserved for the operator.
        assert wt.exists()
        assert _git(
            git_project, "branch", "--list", mgr.branch_name
        ).stdout.strip() != ""

    def test_already_merged_dirty_force_tears_down(
        self, git_project: Path
    ) -> None:
        mgr, wt, _base, _sha = self._setup_already_merged(
            git_project, "sess-596c"
        )
        (wt / "feature.txt").write_text("uncommitted change")
        # force bypasses _check_clean → clean teardown of the already-merged wt.
        mgr.merge(force=True)
        assert not wt.exists()
        assert _git(
            git_project, "branch", "--list", mgr.branch_name
        ).stdout.strip() == ""

    def test_not_merged_wandered_head_still_refuses(
        self, git_project: Path
    ) -> None:
        """Guard intact: a NOT-merged branch + wandered HEAD still raises the
        HATS-533 mismatch — the short-circuit must not mask the real
        wrong-branch-merge risk."""
        mgr = WorktreeManager(git_project, "tester", "sess-596d")
        wt = mgr.create()
        (wt / "feature.txt").write_text("wip")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "wip")
        # NOT merged into base; just wander the main-repo HEAD.
        _git(git_project, "checkout", "-b", "wandered")
        with pytest.raises(WorktreeBaseBranchMismatchError):
            mgr.merge()
        # Branch preserved.
        assert _git(
            git_project, "branch", "--list", mgr.branch_name
        ).stdout.strip() != ""
