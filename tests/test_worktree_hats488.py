"""HATS-488 (+ folded HATS-490): wt teardown hardening.

Covers:
* B-03 — `_remove_worktree` no longer silently nukes residual data;
  raises `WorktreeRemoveError` unless `force_rmtree=True`.
* R-04 — auto-`git worktree prune` removed from the failure fallback;
  test pins that prune is NOT invoked.
* B-06 (HATS-490) — `is_inside_linked_worktree` runs ONE `git rev-parse`
  instead of two.
* CLI surface: `--force-remove` flag plumbs through; `WorktreeRemoveError`
  surfaces as exit 2 with guidance.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import worktrees_dir
from ai_hats_wt import WorktreeManager, WorktreeRemoveError


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


# ---------------------------------------------------------------------------
# B-03 — default refuses silent rmtree; opt-in via force_rmtree=True
# ---------------------------------------------------------------------------


class TestRemoveWorktreeDataPreservation:
    def test_happy_path_no_raise(self, git_project: Path) -> None:
        """`git worktree remove --force` succeeds → no raise, no fallback."""
        mgr = WorktreeManager(git_project, branch_name="task/remove-ok")
        wt_path = mgr.create()
        mgr.save_state()
        try:
            mgr._remove_worktree()
            assert not wt_path.exists()
        finally:
            # If _remove_worktree succeeded, the discard cleanup below
            # finds the branch already absent — branch_name cleanup is
            # in _delete_branch which we still need. Best-effort.
            try:
                _git(git_project, "branch", "-D", "task/remove-ok")
            except subprocess.CalledProcessError:
                pass

    def test_default_raises_when_git_fails_and_dir_exists(self, git_project: Path) -> None:
        """Simulated `git worktree remove --force` failure + dir still on
        disk → WorktreeRemoveError; dir untouched (data preserved)."""
        mgr = WorktreeManager(git_project, branch_name="task/remove-stuck")
        wt_path = mgr.create()
        mgr.save_state()
        try:
            # Mark a sentinel file so we can prove the dir wasn't nuked.
            (wt_path / "DO_NOT_DELETE.txt").write_text("precious\n")

            real_git = mgr._git

            def selective_git(*args, **kwargs):
                if args[:2] == ("worktree", "remove"):
                    raise subprocess.CalledProcessError(
                        1,
                        ["git", *args],
                        stderr="fatal: foo.txt is held open by another process\n",
                    )
                return real_git(*args, **kwargs)

            with patch.object(mgr, "_git", side_effect=selective_git):
                with pytest.raises(WorktreeRemoveError) as exc_info:
                    mgr._remove_worktree()

            assert exc_info.value.path == wt_path
            assert "held open" in exc_info.value.stderr_tail
            # Data preserved.
            assert wt_path.exists()
            assert (wt_path / "DO_NOT_DELETE.txt").exists()
            assert (wt_path / "DO_NOT_DELETE.txt").read_text() == "precious\n"
        finally:
            mgr.discard(force=True, force_remove=True)

    def test_force_rmtree_cleans_with_warning(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`force_rmtree=True` allows the rmtree fallback + emits WARNING."""
        mgr = WorktreeManager(git_project, branch_name="task/remove-force")
        wt_path = mgr.create()
        mgr.save_state()
        try:
            (wt_path / "junk.txt").write_text("nuke me\n")

            real_git = mgr._git

            def selective_git(*args, **kwargs):
                if args[:2] == ("worktree", "remove"):
                    raise subprocess.CalledProcessError(
                        1,
                        ["git", *args],
                        stderr="fatal: cannot remove worktree\n",
                    )
                return real_git(*args, **kwargs)

            with (
                patch.object(mgr, "_git", side_effect=selective_git),
                caplog.at_level(logging.WARNING, logger="ai_hats_wt.manager"),
            ):
                mgr._remove_worktree(force_rmtree=True)

            assert not wt_path.exists()
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert any("force-removing worktree dir" in r.message for r in warnings)
        finally:
            try:
                _git(git_project, "branch", "-D", "task/remove-force")
            except subprocess.CalledProcessError:
                pass

    def test_already_gone_no_raise_no_prune(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`git worktree remove` fails + dir absent → no raise, no prune."""
        mgr = WorktreeManager(git_project, branch_name="task/remove-vanished")
        wt_path = mgr.create()
        mgr.save_state()
        try:
            # Externally yank the dir (simulates a peer cleanup or
            # external removal that races with our remove).
            import shutil

            shutil.rmtree(wt_path)

            real_git = mgr._git
            prune_called = []

            def tracker(*args, **kwargs):
                if args[:2] == ("worktree", "prune"):
                    prune_called.append(args)
                if args[:2] == ("worktree", "remove"):
                    # git would now fail with "not a working tree" or similar.
                    raise subprocess.CalledProcessError(
                        128,
                        ["git", *args],
                        stderr="fatal: not a valid worktree\n",
                    )
                return real_git(*args, **kwargs)

            with (
                patch.object(mgr, "_git", side_effect=tracker),
                caplog.at_level(logging.INFO, logger="ai_hats_wt.manager"),
            ):
                mgr._remove_worktree()  # must NOT raise

            assert not wt_path.exists()
            assert prune_called == [], (
                f"R-04 regression: auto-prune called after failed remove: {prune_called}"
            )
            assert any("already absent" in r.message for r in caplog.records)
        finally:
            try:
                _git(git_project, "branch", "-D", "task/remove-vanished")
            except subprocess.CalledProcessError:
                pass


# ---------------------------------------------------------------------------
# R-04 — auto-`git worktree prune` removed
# ---------------------------------------------------------------------------


class TestNoAutoPrune:
    def test_prune_never_invoked_on_force_rmtree(self, git_project: Path) -> None:
        """Even on the force-rmtree path, the old auto-prune call is gone."""
        mgr = WorktreeManager(git_project, branch_name="task/no-prune")
        mgr.create()
        mgr.save_state()
        try:
            real_git = mgr._git
            prune_called = []

            def tracker(*args, **kwargs):
                if args[:2] == ("worktree", "prune"):
                    prune_called.append(args)
                if args[:2] == ("worktree", "remove"):
                    raise subprocess.CalledProcessError(
                        1,
                        ["git", *args],
                        stderr="fatal: cannot remove\n",
                    )
                return real_git(*args, **kwargs)

            with patch.object(mgr, "_git", side_effect=tracker):
                mgr._remove_worktree(force_rmtree=True)

            assert prune_called == [], f"R-04 regression: auto-prune invoked: {prune_called}"
        finally:
            try:
                _git(git_project, "branch", "-D", "task/no-prune")
            except subprocess.CalledProcessError:
                pass


# ---------------------------------------------------------------------------
# B-06 (HATS-490) — is_inside_linked_worktree single subprocess
# ---------------------------------------------------------------------------


class TestIsInsideLinkedWorktreeSingleRevParse:
    def test_main_worktree_returns_false(self, git_project: Path) -> None:
        """Inside the main worktree, --git-dir == --git-common-dir."""
        assert WorktreeManager.is_inside_linked_worktree(git_project) is False

    def test_linked_worktree_returns_true(self, git_project: Path) -> None:
        """Inside a linked worktree, --git-dir != --git-common-dir."""
        mgr = WorktreeManager(git_project, branch_name="task/inside-linked")
        wt_path = mgr.create()
        try:
            assert WorktreeManager.is_inside_linked_worktree(wt_path) is True
        finally:
            mgr.discard(force=True)

    def test_only_one_subprocess_call(self, git_project: Path) -> None:
        """HATS-490: assert exactly ONE git rev-parse invocation, not two.

        Pre-490 the impl ran two separate subprocess.run calls; post-490
        a single invocation accepts --git-dir + --git-common-dir.
        """
        import ai_hats_wt.manager as wt_mod

        real_run = subprocess.run
        rev_parse_calls = []

        def tracking_run(cmd, *args, **kwargs):
            if (
                isinstance(cmd, list)
                and len(cmd) >= 2
                and cmd[0] == "git"
                and cmd[1] == "rev-parse"
            ):
                rev_parse_calls.append(tuple(cmd))
            return real_run(cmd, *args, **kwargs)

        # Patch the module-bound `subprocess` namespace used by
        # `is_inside_linked_worktree` (it imports subprocess at module
        # top, so patching that attr works).
        with patch.object(wt_mod.subprocess, "run", side_effect=tracking_run):
            WorktreeManager.is_inside_linked_worktree(git_project)

        assert len(rev_parse_calls) == 1, (
            f"expected exactly 1 git rev-parse call, got {len(rev_parse_calls)}: {rev_parse_calls}"
        )
        # Single call carries BOTH flags.
        cmd = rev_parse_calls[0]
        assert "--git-dir" in cmd
        assert "--git-common-dir" in cmd

    def test_git_missing_returns_false(self) -> None:
        """FileNotFoundError handled the same as CalledProcessError."""
        import ai_hats_wt.manager as wt_mod

        def boom(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory: 'git'")

        with patch.object(wt_mod.subprocess, "run", side_effect=boom):
            assert WorktreeManager.is_inside_linked_worktree(Path("/tmp")) is False


# ---------------------------------------------------------------------------
# CLI: --force-remove flag + WorktreeRemoveError handling
# ---------------------------------------------------------------------------


class TestCliForceRemoveFlag:
    def test_discard_default_partial_remove_exits_2(self, git_project: Path, monkeypatch) -> None:
        """`wt discard` w/o --force-remove + simulated stuck dir → exit 2."""
        runner = CliRunner()
        monkeypatch.chdir(git_project)

        mgr = WorktreeManager(
            git_project,
            branch_name="task/cli-stuck",
            state_dir=worktrees_dir(git_project),
        )
        wt_path = mgr.create()
        mgr.save_state()

        # Patch _remove_worktree on the class so the CLI's freshly-loaded
        # manager invokes our raise.
        def boom(self, *, force_rmtree: bool = False) -> None:
            if not force_rmtree:
                raise WorktreeRemoveError(wt_path, "fatal: cannot remove (held open)")

        try:
            monkeypatch.setattr(WorktreeManager, "_remove_worktree", boom)
            result = runner.invoke(main, ["wt", "discard", "task/cli-stuck"])
            assert result.exit_code == 2, result.output
            assert "Refused to remove worktree dir" in result.output
            assert "--force-remove" in result.output
        finally:
            # Real cleanup.
            monkeypatch.undo()
            mgr.discard(force=True, force_remove=True)

    def test_discard_force_remove_succeeds(self, git_project: Path, monkeypatch) -> None:
        """`wt discard --force-remove` plumbs `force_rmtree=True`."""
        runner = CliRunner()
        monkeypatch.chdir(git_project)

        mgr = WorktreeManager(
            git_project,
            branch_name="task/cli-force-rm",
            state_dir=worktrees_dir(git_project),
        )
        wt_path = mgr.create()
        mgr.save_state()

        seen_force = {}

        real_remove = WorktreeManager._remove_worktree

        def tracker(self, *, force_rmtree: bool = False) -> None:
            seen_force["value"] = force_rmtree
            return real_remove(self, force_rmtree=force_rmtree)

        try:
            monkeypatch.setattr(WorktreeManager, "_remove_worktree", tracker)
            result = runner.invoke(main, ["wt", "discard", "task/cli-force-rm", "--force-remove"])
            assert result.exit_code == 0, result.output
            assert seen_force.get("value") is True, (
                f"--force-remove did NOT plumb into _remove_worktree: {seen_force}"
            )
        finally:
            monkeypatch.undo()
            assert not wt_path.exists()
