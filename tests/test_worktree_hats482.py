"""HATS-482 rollup: quick-win operator-error guards.

Covers B-02 (_delete_branch classification + WorktreePartialCleanupError),
B-07 (case-preserving _state_key + legacy migration + soft CLI input regex),
B-08 (guard for wt merge/discard/list from inside linked worktree),
R-08 (fail-on-ambiguity in _resolve_worktree).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli.worktree import _resolve_worktree
from ai_hats.worktree import (
    WorktreeManager,
    WorktreePartialCleanupError,
    _state_key,
)
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
# B-07 — case-preserving _state_key
# ---------------------------------------------------------------------------


class TestStateKeyCasePreserving:
    def test_state_key_preserves_case(self) -> None:
        assert _state_key("Task/HATS-X") == "Task-HATS-X"
        assert _state_key("task/hats-x") == "task-hats-x"
        # Distinct refs → distinct keys.
        assert _state_key("Task/HATS-X") != _state_key("task/hats-x")

    def test_state_key_lowercase_still_lowercase(self) -> None:
        # Backwards compat for the common case: lowercase in → lowercase out.
        assert _state_key("task/hats-001") == "task-hats-001"

    def test_legacy_lowercase_state_migrates_on_load(self, git_project: Path) -> None:
        """Pre-482 lowercased state files migrate to the case-preserving key."""
        states = worktrees_dir(git_project)
        states.mkdir(parents=True, exist_ok=True)
        legacy_path = states / "task-hats-x.json"
        # Worktree path doesn't need to exist — _load_by_key returns None
        # when wt_path is missing, but does so AFTER the migration rename.
        legacy_path.write_text(
            json.dumps(
                {
                    "branch": "task/HATS-X",
                    "worktree_path": "/nonexistent",
                    "original_branch": "master",
                    "base_sha_at_create": None,
                }
            )
        )

        # Load via the new (case-preserving) key → migration triggers.
        result = WorktreeManager.load_for_branch(
            git_project, "task/HATS-X", state_dir=worktrees_dir(git_project)
        )

        # File renamed; legacy gone, primary key file existed at migration
        # time (then unlinked by _load_by_key's stale-path cleanup).
        assert not legacy_path.exists()
        # _load_by_key returned None because wt path doesn't exist → file
        # was cleaned up. The migration step itself is what we verify.
        assert result is None

    def test_no_migration_when_primary_exists(self, git_project: Path) -> None:
        """Primary key file present → don't touch legacy."""
        states = worktrees_dir(git_project)
        states.mkdir(parents=True, exist_ok=True)
        legacy = states / "task-hats-x.json"
        primary = states / "task-HATS-X.json"
        legacy.write_text(json.dumps({"branch": "task/hats-x"}))  # different ref
        primary.write_text(
            json.dumps(
                {
                    "branch": "task/HATS-X",
                    "worktree_path": "/nonexistent",
                }
            )
        )

        WorktreeManager._migrate_legacy_lowercase_state(primary, "task-HATS-X")

        assert legacy.exists()
        assert primary.exists()

    def test_no_migration_when_key_already_lowercase(self, git_project: Path) -> None:
        """key.lower() == key → nothing to migrate, no-op."""
        states = worktrees_dir(git_project)
        states.mkdir(parents=True, exist_ok=True)
        primary = states / "task-hats-x.json"
        # Don't create the file — function should no-op before any I/O.

        WorktreeManager._migrate_legacy_lowercase_state(primary, "task-hats-x")

        assert not primary.exists()

    def test_load_for_task_uppercase_id_finds_lowercase_branch_file(
        self, git_project: Path
    ) -> None:
        """REGRESSION (audit-review): state.py:763 creates branches as
        ``f"task/{task.id.lower()}"``. The canonical state file for task
        HATS-086 lives at ``task-hats-086.json`` (state.py's branch
        construction + post-482 case-preserving _state_key). Therefore
        ``load_for_task("HATS-086")`` MUST mirror state.py and lowercase
        the task_id before key derivation — otherwise the lookup key
        ``task-HATS-086`` wouldn't match.

        Filesystem note: this test runs on case-insensitive APFS by
        default on macOS, where ``Path("task-HATS-086.json").exists()``
        returns True if ``task-hats-086.json`` exists (same inode). The
        substantive invariant is therefore: (a) load returns the right
        manager, AND (b) the state directory stays at exactly one entry
        across save + load + save round-trips (no orphaned alternate-key
        file lurking on case-sensitive FS like Linux ext4)."""
        mgr = WorktreeManager(
            git_project,
            branch_name="task/hats-086",
            state_dir=worktrees_dir(git_project),
        )
        mgr.create()
        mgr.save_state()
        try:
            states = worktrees_dir(git_project)
            before = sorted(p.name for p in states.iterdir())
            assert before == ["task-hats-086.json"], before

            # Uppercase task_id (project convention) must resolve to the
            # same file. Round-trip save again — load_for_task must point
            # at the SAME key so save_state doesn't fork into a second
            # file on case-sensitive FS.
            loaded = WorktreeManager.load_for_task(
                git_project, "HATS-086", state_dir=worktrees_dir(git_project)
            )
            assert loaded is not None
            assert loaded.branch_name == "task/hats-086"
            loaded.save_state()

            after = sorted(p.name for p in states.iterdir())
            assert after == ["task-hats-086.json"], (
                f"save_state after load_for_task forked into a second key "
                f"on case-sensitive FS — load_for_task didn't match the "
                f"canonical branch-derived key. Got: {after}"
            )
        finally:
            mgr.discard(force=True)


# ---------------------------------------------------------------------------
# B-07 — soft CLI branch-name validation
# ---------------------------------------------------------------------------


class TestBranchNameValidation:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    @pytest.mark.parametrize(
        "name",
        [
            "task/hats-001",  # canonical lowercase
            "Task/HATS-X",  # mixed case allowed (B-07 permissive)
            "feat/foo_bar.baz",  # underscore + dot
            "a",  # single char
        ],
    )
    def test_accepts_valid_names(self, runner: CliRunner, git_project: Path, name: str) -> None:
        with runner.isolated_filesystem(temp_dir=git_project.parent):
            import os

            os.chdir(git_project)
            result = runner.invoke(main, ["wt", "create", name])
            # Either success or a real worktree error — but NOT a click
            # BadParameter (which would be exit 2 with "Invalid value").
            assert "Invalid branch name" not in result.output

    @pytest.mark.parametrize(
        "name,why",
        [
            ("../escape", "no '..'"),
            (".hidden", "leading dot"),
            ("/leading-slash", "leading slash"),
            ("has space", "no whitespace"),
        ],
    )
    def test_rejects_bad_names(
        self, runner: CliRunner, git_project: Path, name: str, why: str
    ) -> None:
        """Names that violate our regex AND survive click's own arg parsing."""
        import os

        os.chdir(git_project)
        # `--` separator so click treats next arg as positional even if it
        # starts with `-`. Mirrors how operators would invoke from a shell.
        result = runner.invoke(main, ["wt", "create", "--", name])
        assert result.exit_code != 0
        assert "Invalid branch name" in result.output, (
            f"name={name!r} ({why}); output={result.output!r}"
        )

    def test_empty_branch_rejected_by_click(self, runner: CliRunner, git_project: Path) -> None:
        """Empty string surfaces as our regex rejection (matches `^[A-Za-z0-9]`)."""
        import os

        os.chdir(git_project)
        result = runner.invoke(main, ["wt", "create", "--", ""])
        assert result.exit_code != 0
        assert "Invalid branch name" in result.output

    def test_leading_dash_rejected(self, runner: CliRunner, git_project: Path) -> None:
        """Leading dash rejected (via --) even though shell would otherwise
        confuse it with an option."""
        import os

        os.chdir(git_project)
        result = runner.invoke(main, ["wt", "create", "--", "-dash-leading"])
        assert result.exit_code != 0
        assert "Invalid branch name" in result.output


# ---------------------------------------------------------------------------
# R-08 — fail-on-ambiguity in _resolve_worktree
# ---------------------------------------------------------------------------


class TestResolveWorktreeAmbiguity:
    def test_zero_active_returns_none(self, git_project: Path, monkeypatch) -> None:
        monkeypatch.chdir(git_project)
        assert _resolve_worktree(None) is None

    def test_one_active_returns_it(self, git_project: Path, monkeypatch) -> None:
        monkeypatch.chdir(git_project)
        mgr = WorktreeManager(
            git_project,
            branch_name="feat/only-one",
            state_dir=worktrees_dir(git_project),
        )
        mgr.create()
        mgr.save_state()
        try:
            resolved = _resolve_worktree(None)
            assert resolved is not None
            assert resolved.branch_name == "feat/only-one"
        finally:
            mgr.discard(force=True)

    def test_two_active_raises_usage_error(self, git_project: Path, monkeypatch) -> None:
        import click

        monkeypatch.chdir(git_project)
        mgr_a = WorktreeManager(
            git_project,
            branch_name="feat/aaa",
            state_dir=worktrees_dir(git_project),
        )
        mgr_a.create()
        mgr_a.save_state()
        mgr_b = WorktreeManager(
            git_project,
            branch_name="feat/bbb",
            state_dir=worktrees_dir(git_project),
        )
        mgr_b.create()
        mgr_b.save_state()
        try:
            with pytest.raises(click.UsageError) as exc_info:
                _resolve_worktree(None)
            assert "Multiple active worktrees" in str(exc_info.value)
            assert "feat/aaa" in str(exc_info.value)
            assert "feat/bbb" in str(exc_info.value)
        finally:
            mgr_a.discard(force=True)
            mgr_b.discard(force=True)


# ---------------------------------------------------------------------------
# B-08 — guard for merge/discard/list from inside linked worktree
# ---------------------------------------------------------------------------


class TestLinkedWorktreeGuard:
    """`wt merge`/`discard`/`list` refuse when CWD is in a linked worktree.

    Direct unit test on the helper (mocked is_inside_linked_worktree) +
    CliRunner test via main entry point with monkeypatched detection.
    """

    def test_helper_exits_when_inside_linked_worktree(self, git_project: Path, monkeypatch) -> None:
        from ai_hats.cli._helpers import _guard_not_inside_linked_worktree

        monkeypatch.setattr(
            "ai_hats.worktree.WorktreeManager.is_inside_linked_worktree",
            staticmethod(lambda _path: True),
        )
        # HATS-788: guard takes no arg now — it checks the raw Path.cwd().
        with pytest.raises(SystemExit) as exc_info:
            _guard_not_inside_linked_worktree()
        assert exc_info.value.code == 1

    def test_helper_passes_when_not_inside(self, git_project: Path, monkeypatch) -> None:
        from ai_hats.cli._helpers import _guard_not_inside_linked_worktree

        monkeypatch.setattr(
            "ai_hats.worktree.WorktreeManager.is_inside_linked_worktree",
            staticmethod(lambda _path: False),
        )
        # No exit, no exception.
        assert _guard_not_inside_linked_worktree() is None

    @pytest.mark.parametrize("subcmd", ["merge", "discard", "list"])
    def test_cli_refuses_from_inside_linked_worktree(
        self, git_project: Path, monkeypatch, subcmd: str
    ) -> None:
        runner = CliRunner()
        monkeypatch.chdir(git_project)
        monkeypatch.setattr(
            "ai_hats.worktree.WorktreeManager.is_inside_linked_worktree",
            staticmethod(lambda _path: True),
        )
        result = runner.invoke(main, ["wt", subcmd])
        assert result.exit_code == 1
        assert "Cannot run this command from inside a linked worktree" in result.output


# ---------------------------------------------------------------------------
# B-02 — _delete_branch classification + WorktreePartialCleanupError
# ---------------------------------------------------------------------------


class TestDeleteBranchClassification:
    def test_unknown_stderr_stays_silent_no_raise(self, git_project: Path, caplog) -> None:
        """Regression-safe path: random git error stays DEBUG, no raise."""
        mgr = WorktreeManager(git_project, branch_name="feat/silent-test")
        mgr.create()
        mgr.save_state()
        try:
            # Mock _git to raise CalledProcessError with non-classified stderr.
            with patch.object(mgr, "_git") as mock_git:
                mock_git.side_effect = subprocess.CalledProcessError(
                    1,
                    ["git", "branch", "-D", "feat/silent-test"],
                    stderr="error: random unexplained git failure\n",
                )
                # Should NOT raise.
                mgr._delete_branch()
        finally:
            # Real cleanup outside the mock.
            mgr.discard(force=True)

    @pytest.mark.parametrize(
        "stderr,expected_reason",
        [
            ("error: branch 'foo' is not fully merged.\n", "not_fully_merged"),
            ("error: cannot delete branch used by worktree at /tmp/x\n", "checked_out"),
            ("error: branch is the current branch of checkout /tmp/x\n", "checked_out"),
            ("error: cannot lock ref 'refs/heads/foo'\n", "locked"),
            ("error: unable to lock ref\n", "locked"),
        ],
    )
    def test_classified_stderr_raises(
        self,
        git_project: Path,
        stderr: str,
        expected_reason: str,
    ) -> None:
        mgr = WorktreeManager(git_project, branch_name="feat/classify-test")
        mgr.create()
        mgr.save_state()
        try:
            with patch.object(mgr, "_git") as mock_git:
                mock_git.side_effect = subprocess.CalledProcessError(
                    1,
                    ["git", "branch", "-D", "feat/classify-test"],
                    stderr=stderr,
                )
                with pytest.raises(WorktreePartialCleanupError) as exc_info:
                    mgr._delete_branch()
                assert exc_info.value.reason == expected_reason
                assert exc_info.value.branch_name == "feat/classify-test"
                assert exc_info.value.stderr_tail  # non-empty
        finally:
            mgr.discard(force=True)

    def test_cli_wt_discard_handles_partial_cleanup(self, git_project: Path, monkeypatch) -> None:
        """wt discard → exit 2 + guidance message on WorktreePartialCleanupError."""
        runner = CliRunner()
        monkeypatch.chdir(git_project)

        mgr = WorktreeManager(
            git_project,
            branch_name="feat/cli-partial",
            state_dir=worktrees_dir(git_project),
        )
        mgr.create()
        mgr.save_state()

        try:
            # Patch _delete_branch to raise; _remove_worktree runs unhindered
            # so the worktree dir is gone (matches the partial-cleanup contract).
            def boom(self):
                raise WorktreePartialCleanupError(
                    "feat/cli-partial",
                    "checked_out",
                    "fatal: branch is used by worktree",
                )

            monkeypatch.setattr(WorktreeManager, "_delete_branch", boom)

            result = runner.invoke(main, ["wt", "discard", "feat/cli-partial"])
            assert result.exit_code == 2
            assert "branch 'feat/cli-partial' preserved" in result.output
            assert "checked_out" in result.output
            assert "Manual cleanup" in result.output
        finally:
            # Force-clean state (branch already torn down or doesn't matter).
            try:
                _git(git_project, "branch", "-D", "feat/cli-partial")
            except subprocess.CalledProcessError:
                pass
