"""Tests for agent-driven worktree flow: persistent create/merge/discard/list."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.worktree import WorktreeManager
from ai_hats.paths import worktrees_dir


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
        mgr = WorktreeManager(
            git_project,
            branch_name="feat/test-save",
            state_dir=worktrees_dir(git_project),
        )
        mgr.create()
        state_path = mgr.save_state()
        try:
            assert state_path.exists()
            assert state_path.parent.name == "worktrees"
            data = json.loads(state_path.read_text())
            assert data["branch"] == "feat/test-save"
            assert Path(data["worktree_path"]).is_dir()
            assert data["original_branch"]
        finally:
            mgr.cleanup()

    def test_load_for_branch_restores_state(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/test-load")
        wt = mgr.create()
        mgr.save_state()

        loaded = WorktreeManager.load_for_branch(git_project, "feat/test-load")
        try:
            assert loaded is not None
            assert loaded.branch_name == "feat/test-load"
            assert loaded.worktree_path == wt
            assert loaded.worktree_path.is_dir()
        finally:
            loaded.cleanup()

    def test_load_for_branch_returns_none_when_stale(self, git_project: Path) -> None:
        """If worktree dir was deleted externally, load cleans up."""
        states_dir = worktrees_dir(git_project)
        states_dir.mkdir(parents=True, exist_ok=True)
        state_path = states_dir / "feat-stale.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat/stale",
                    "worktree_path": "/tmp/nonexistent-worktree-12345",
                    "original_branch": "master",
                }
            )
        )
        assert (
            WorktreeManager.load_for_branch(
                git_project, "feat/stale", state_dir=worktrees_dir(git_project)
            )
            is None
        )
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Persistent merge flow (create → save → load → merge)
# ---------------------------------------------------------------------------


class TestPersistentMerge:
    def test_create_save_load_merge(self, git_project: Path) -> None:
        """Full agent flow: create → work → save → load → merge."""
        mgr = WorktreeManager(git_project, branch_name="feat/agent-task")
        wt = mgr.create()
        mgr.save_state()

        (wt / "result.txt").write_text("done")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "complete task")

        mgr2 = WorktreeManager.load_for_branch(git_project, "feat/agent-task")
        assert mgr2 is not None
        mgr2.merge(squash=True)

        assert (git_project / "result.txt").read_text() == "done"
        # State file is gone
        assert not (worktrees_dir(git_project) / "feat-agent-task.json").exists()
        assert not wt.exists()

    def test_create_save_load_discard(self, git_project: Path) -> None:
        """Agent decides to discard work."""
        mgr = WorktreeManager(git_project, branch_name="feat/bad-idea")
        wt = mgr.create()
        mgr.save_state()

        (wt / "junk.txt").write_text("nope")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "bad commit")

        mgr2 = WorktreeManager.load_for_branch(git_project, "feat/bad-idea")
        assert mgr2 is not None
        mgr2.discard()

        assert not (git_project / "junk.txt").exists()
        assert not wt.exists()
        assert not (worktrees_dir(git_project) / "feat-bad-idea.json").exists()

    def test_merge_no_squash(self, git_project: Path) -> None:
        """Regular merge (not squash)."""
        mgr = WorktreeManager(git_project, branch_name="feat/full-merge")
        wt = mgr.create()
        mgr.save_state()

        (wt / "merged.txt").write_text("merged")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "a change")

        mgr2 = WorktreeManager.load_for_branch(git_project, "feat/full-merge")
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


# ---------------------------------------------------------------------------
# wt exec / wt env (HATS-089 category A: worktree boilerplate replacement)
# ---------------------------------------------------------------------------


class _FakeCompleted:
    """Stand-in for subprocess.CompletedProcess used by wt_exec."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


@pytest.fixture
def active_worktree(git_project: Path, monkeypatch):
    """Create a worktree, save state, and chdir to project. Auto-cleanup."""
    monkeypatch.chdir(git_project)
    mgr = WorktreeManager(
        git_project,
        branch_name="feat/exec-test",
        state_dir=worktrees_dir(git_project),
    )
    wt = mgr.create()
    mgr.save_state()
    yield git_project, wt
    mgr.cleanup()


class TestWtExec:
    def _patch_subprocess(self, monkeypatch, fake_run):
        """Patch subprocess.run and prevent is_inside_linked_worktree from using it."""
        monkeypatch.setattr("ai_hats.cli.worktree.subprocess.run", fake_run)
        monkeypatch.setattr(
            WorktreeManager, "is_inside_linked_worktree", staticmethod(lambda _: False)
        )

    def test_runs_in_worktree_cwd_with_pythonpath(self, active_worktree, monkeypatch) -> None:
        project, wt = active_worktree
        captured: dict = {}

        def fake_run(cmd, cwd=None, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["env"] = env
            return _FakeCompleted(returncode=0)

        self._patch_subprocess(monkeypatch, fake_run)
        runner = CliRunner()
        result = runner.invoke(main, ["wt", "exec", "--", "pytest", "-xvs"])

        assert result.exit_code == 0, result.output
        assert captured["cmd"] == ["pytest", "-xvs"]
        assert captured["cwd"] == str(wt)
        assert captured["env"]["PYTHONPATH"].startswith(f"{wt}/src")

    def test_pythonpath_prepends_existing(self, active_worktree, monkeypatch) -> None:
        project, wt = active_worktree
        monkeypatch.setenv("PYTHONPATH", "/pre/existing")
        captured: dict = {}

        def fake_run(cmd, cwd=None, env=None, **kwargs):
            captured["env"] = env
            return _FakeCompleted(returncode=0)

        self._patch_subprocess(monkeypatch, fake_run)
        runner = CliRunner()
        runner.invoke(main, ["wt", "exec", "--", "true"])

        assert captured["env"]["PYTHONPATH"] == f"{wt}/src:/pre/existing"

    def test_propagates_exit_code(self, active_worktree, monkeypatch) -> None:
        def fake_run(cmd, cwd=None, env=None, **kwargs):
            return _FakeCompleted(returncode=42)

        self._patch_subprocess(monkeypatch, fake_run)
        runner = CliRunner()
        result = runner.invoke(main, ["wt", "exec", "--", "false"])
        assert result.exit_code == 42

    def test_no_active_worktree_exits_1(self, git_project: Path, monkeypatch) -> None:
        monkeypatch.chdir(git_project)
        runner = CliRunner()
        result = runner.invoke(main, ["wt", "exec", "--", "echo", "hi"])
        assert result.exit_code == 1
        assert "No active worktree" in result.output

    def test_command_not_found_exits_127(self, git_project: Path, monkeypatch) -> None:
        # Self-contained: create + run + cleanup before any patch teardown,
        # so the fake subprocess.run never collides with worktree cleanup.
        monkeypatch.chdir(git_project)
        mgr = WorktreeManager(
            git_project,
            branch_name="feat/notfound-test",
            state_dir=worktrees_dir(git_project),
        )
        mgr.create()
        mgr.save_state()
        try:
            from unittest.mock import patch

            def fake_run(cmd, cwd=None, env=None, **kwargs):
                raise FileNotFoundError(2, "not found", "missing-binary")

            with patch("ai_hats.cli.worktree.subprocess.run", fake_run):
                runner = CliRunner()
                result = runner.invoke(main, ["wt", "exec", "--", "missing-binary"])
            assert result.exit_code == 127
            assert "missing-binary" in result.output
        finally:
            mgr.cleanup()


class TestWtEnv:
    def test_outputs_exports(self, active_worktree) -> None:
        project, wt = active_worktree
        runner = CliRunner()
        result = runner.invoke(main, ["wt", "env"])

        assert result.exit_code == 0
        assert f'export WT="{wt}"' in result.output
        assert f'export PYTHONPATH="{wt}/src' in result.output

    def test_no_active_worktree_exits_1(self, git_project: Path, monkeypatch) -> None:
        monkeypatch.chdir(git_project)
        runner = CliRunner()
        result = runner.invoke(main, ["wt", "env"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Per-task worktree registry (HATS-061)
# ---------------------------------------------------------------------------


class TestPerTaskRegistry:
    def test_save_and_load_for_task(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="task/hats-086")
        mgr.create()
        mgr.save_state()
        try:
            loaded = WorktreeManager.load_for_task(git_project, "hats-086")
            assert loaded is not None
            assert loaded.branch_name == "task/hats-086"
        finally:
            mgr.cleanup()

    def test_save_and_load_for_branch(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/hats-060-foo")
        mgr.create()
        mgr.save_state()
        try:
            loaded = WorktreeManager.load_for_branch(git_project, "feat/hats-060-foo")
            assert loaded is not None
            assert loaded.branch_name == "feat/hats-060-foo"
        finally:
            mgr.cleanup()

    def test_list_active_returns_all(self, git_project: Path) -> None:
        mgr1 = WorktreeManager(git_project, branch_name="task/t-1")
        mgr2 = WorktreeManager(git_project, branch_name="task/t-2")
        mgr1.create()
        mgr1.save_state()
        mgr2.create()
        mgr2.save_state()
        try:
            active = WorktreeManager.list_active(git_project)
            assert len(active) == 2
            branches = {m.branch_name for m in active}
            assert branches == {"task/t-1", "task/t-2"}
        finally:
            mgr1.cleanup()
            mgr2.cleanup()

    def test_list_active_prunes_stale(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="task/stale")
        mgr.create()
        mgr.save_state()
        # Remove worktree directory externally
        import shutil

        shutil.rmtree(mgr.worktree_path)
        active = WorktreeManager.list_active(git_project)
        assert len(active) == 0

    def test_clear_state_per_key(self, git_project: Path) -> None:
        mgr1 = WorktreeManager(git_project, branch_name="task/keep")
        mgr2 = WorktreeManager(git_project, branch_name="task/remove")
        mgr1.create()
        mgr1.save_state()
        mgr2.create()
        mgr2.save_state()

        try:
            mgr2._clear_state()
            assert WorktreeManager.load_for_branch(git_project, "task/keep") is not None
            assert WorktreeManager.load_for_branch(git_project, "task/remove") is None
        finally:
            mgr1.cleanup()
            mgr2.cleanup()


# ---------------------------------------------------------------------------
# Dirty worktree safety (HATS-062)
# ---------------------------------------------------------------------------


class TestDirtyWorktreeSafety:
    def test_discard_refuses_on_dirty_worktree(self, git_project: Path) -> None:
        from ai_hats.worktree import WorktreeDirtyError

        mgr = WorktreeManager(git_project, branch_name="feat/dirty-discard")
        wt = mgr.create()
        mgr.save_state()

        # Create uncommitted change
        (wt / "unsaved.txt").write_text("work in progress")

        try:
            with pytest.raises(WorktreeDirtyError, match="uncommitted changes"):
                mgr.discard()
            # Worktree still exists
            assert wt.exists()
        finally:
            mgr.discard(force=True)

    def test_discard_force_overrides(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/dirty-force")
        wt = mgr.create()
        mgr.save_state()

        (wt / "unsaved.txt").write_text("will be lost")
        mgr.discard(force=True)

        assert not wt.exists()

    def test_discard_clean_worktree_succeeds(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/clean-discard")
        wt = mgr.create()
        mgr.save_state()

        # Committed change — worktree is clean
        (wt / "committed.txt").write_text("done")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "save work")

        mgr.discard()
        assert not wt.exists()

    def test_merge_refuses_on_dirty_worktree(self, git_project: Path) -> None:
        from ai_hats.worktree import WorktreeDirtyError

        mgr = WorktreeManager(git_project, branch_name="feat/dirty-merge")
        wt = mgr.create()
        mgr.save_state()

        (wt / "committed.txt").write_text("saved")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "saved work")
        # Then add uncommitted change
        (wt / "unsaved.txt").write_text("not committed")

        try:
            with pytest.raises(WorktreeDirtyError, match="uncommitted changes"):
                mgr.merge()
            assert wt.exists()
        finally:
            mgr.discard(force=True)

    def test_merge_force_overrides(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/dirty-merge-force")
        wt = mgr.create()
        mgr.save_state()

        (wt / "saved.txt").write_text("committed")
        _git(wt, "add", ".")
        _git(wt, "commit", "-m", "save")
        (wt / "extra.txt").write_text("uncommitted")

        mgr.merge(force=True)
        assert not wt.exists()
        # Committed work merged
        assert (git_project / "saved.txt").exists()
