"""HATS-1332: a pruned worktree admin entry is an already-removed worktree.

``git worktree remove`` on a path git no longer tracks fails with "is not a
working tree" — the SUCCESS condition for a teardown — yet pre-1332
``_remove_worktree`` treated every non-zero exit as fatal. The contract is
git's own bookkeeping, so every test drives a real ``git worktree prune``.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_hats.wt_effects import WtWorktreeEffects
from ai_hats_wt import WorktreeManager


pytestmark = pytest.mark.integration


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


def _reap(worktree_path: Path) -> None:
    """Empty the worktree the way the tmp reaper does: files gone, dirs stay."""
    for child in worktree_path.rglob("*"):
        if child.is_file() or child.is_symlink():
            child.unlink()


def _prune_behind_manager(project: Path, worktree_path: Path, *, keep: str = "") -> None:
    """Reap + ``git worktree prune`` — the admin entry goes, the shell stays.

    ``keep`` re-creates one file afterwards: a shell that still holds content.
    """
    _reap(worktree_path)
    _git(project, "worktree", "prune")
    if keep:
        (worktree_path / keep).write_text("unmerged work\n")
    assert worktree_path.exists(), "precondition: the directory shell survives prune"
    listed = [Path(e["path"]).resolve() for e in WorktreeManager.list_worktrees(project)]
    assert worktree_path.resolve() not in listed, "precondition: admin entry pruned"


class TestDiscardAfterPrune:
    def test_empty_shell_discarded_without_raising(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The incident: discard on a pruned worktree completes and cleans up."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/pruned-discard",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
        )
        wt_path = mgr.create()
        mgr.save_state()
        _prune_behind_manager(git_project, wt_path)

        with caplog.at_level(logging.INFO, logger="ai_hats_wt.manager"):
            mgr.discard(force=True)  # must NOT raise

        assert not wt_path.exists(), "the leftover shell holds no files — it is removed"
        assert mgr.worktree_path is None
        assert not WorktreeManager.branch_exists(git_project, "task/pruned-discard")

    def test_shell_holding_files_is_preserved(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HATS-488/B-03 still holds: content git cannot see is never nuked."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/pruned-keep",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
        )
        wt_path = mgr.create()
        mgr.save_state()
        _prune_behind_manager(git_project, wt_path, keep="notes.txt")

        with caplog.at_level(logging.WARNING, logger="ai_hats_wt.manager"):
            mgr.discard(force=True)  # must NOT raise

        assert (wt_path / "notes.txt").read_text() == "unmerged work\n"
        assert any("still" in r.message and "holds files" in r.message for r in caplog.records)
        shutil.rmtree(wt_path)

    def test_force_remove_clears_a_shell_holding_files(self, git_project: Path) -> None:
        """``--force-remove`` keeps its promise on the unregistered path too."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/pruned-force",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
        )
        wt_path = mgr.create()
        mgr.save_state()
        _prune_behind_manager(git_project, wt_path, keep="notes.txt")

        mgr.discard(force=True, force_remove=True)

        assert not wt_path.exists()


class _PruningLifecycle:
    """Races a reaper + ``git worktree prune`` in just before teardown."""

    def __init__(self, project: Path) -> None:
        self.project = project

    def on_created(self, ctx) -> None:  # noqa: ANN001 — core Protocol, ctx unused
        return None

    def before_merge(self, ctx) -> None:  # noqa: ANN001 — HATS-1540, nothing to gate here
        return None

    def before_teardown(self, event: str, ctx) -> None:  # noqa: ANN001
        _prune_behind_manager(self.project, ctx.worktree_path)


class TestMergeAfterPrune:
    def test_merge_completes_when_the_entry_vanishes_mid_teardown(self, git_project: Path) -> None:
        """R4: the merge landed — teardown bookkeeping must not undo the transition."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/pruned-merge",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
            lifecycle=_PruningLifecycle(git_project),
        )
        wt_path = mgr.create()
        mgr.save_state()
        (wt_path / "feature.txt").write_text("shipped\n")
        _git(wt_path, "add", ".")
        _git(wt_path, "commit", "-m", "feature")

        mgr.merge()  # must NOT raise: the prune lands after the merge

        assert (git_project / "feature.txt").read_text() == "shipped\n"
        assert not wt_path.exists()
        assert not WorktreeManager.branch_exists(git_project, "task/pruned-merge")


class TestAdministrativeTeardown:
    """The incident's own entry point: `rack transition <ID> --state cancelled`."""

    def test_cancel_on_a_pruned_worktree_is_quiet(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        mgr = WorktreeManager(
            git_project,
            branch_name="task/hats-9332",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
        )
        wt_path = mgr.create()
        mgr.save_state()
        _prune_behind_manager(git_project, wt_path)

        with caplog.at_level(logging.WARNING):
            outcome = WtWorktreeEffects(ProjectLayout.at(git_project)).teardown(
                "HATS-9332", merge=False
            )

        assert outcome == "discarded"
        assert not wt_path.exists()
        assert [r for r in caplog.records if r.exc_info] == [], "no traceback for a clean teardown"

    def test_a_real_discard_failure_is_reported_without_a_stack_trace(
        self, git_project: Path, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        """R5: swallowed ≠ silent, but a succeeded transition shows no traceback."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/hats-9333",
            state_dir=ProjectLayout.at(git_project).sessions.worktrees,
        )
        mgr.create()
        mgr.save_state()

        def boom(self, **kwargs) -> None:
            raise RuntimeError("git is having a day")

        monkeypatch.setattr(WorktreeManager, "discard", boom)
        with caplog.at_level(logging.WARNING):
            outcome = WtWorktreeEffects(ProjectLayout.at(git_project)).teardown(
                "HATS-9333", merge=False
            )

        assert outcome is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("task/hats-9333" in r.getMessage() for r in warnings)
        assert any("git is having a day" in r.getMessage() for r in warnings)
        assert [r for r in warnings if r.exc_info] == [], "one line, not 9 frames"
        monkeypatch.undo()
        mgr.discard(force=True, force_remove=True)
