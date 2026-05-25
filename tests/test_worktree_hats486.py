"""HATS-486: stale .git/index.lock detection (warn-only v1).

Covers:
* `_stale_index_lock_age` pure-function semantics: fresh-or-missing → None,
  past-threshold → (age, path), git-missing → None, linked-worktree-aware.
* Integration through `_retry_git_merge`: on first retriable git error,
  if the lock is stale, a WARNING is emitted with the rm -f hint —
  but ONLY when project_dir is plumbed through (backwards-compat).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_hats.worktree import (
    STALE_INDEX_LOCK_THRESHOLD_S,
    WorktreeManager,
    _retry_git_merge,
    _stale_index_lock_age,
)


pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
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


def _backdate(path: Path, seconds_ago: float) -> None:
    """Set ``path`` mtime to ``seconds_ago`` seconds in the past."""
    target = time.time() - seconds_ago
    os.utime(path, (target, target))


# ---------------------------------------------------------------------------
# _stale_index_lock_age — pure function
# ---------------------------------------------------------------------------


class TestStaleIndexLockAge:
    def test_no_lock_returns_none(self, git_project: Path) -> None:
        """index.lock missing → None (happy path)."""
        assert _stale_index_lock_age(git_project) is None

    def test_fresh_lock_returns_none(self, git_project: Path) -> None:
        """Lock exists but young (< threshold) → None (legit in-progress)."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("fresh-merge-in-progress\n")
        try:
            # mtime is "now" — well under threshold.
            assert _stale_index_lock_age(git_project) is None
        finally:
            lock.unlink()

    def test_stale_lock_returns_age_and_path(self, git_project: Path) -> None:
        """Lock older than threshold → (age, path) tuple."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("crashed\n")
        try:
            _backdate(lock, seconds_ago=STALE_INDEX_LOCK_THRESHOLD_S + 30)
            result = _stale_index_lock_age(git_project)
            assert result is not None
            age, path = result
            assert age >= STALE_INDEX_LOCK_THRESHOLD_S + 30 - 5  # slack
            assert path.name == "index.lock"
            assert path.resolve() == lock.resolve()
        finally:
            lock.unlink()

    def test_custom_threshold(self, git_project: Path) -> None:
        """threshold_s kwarg overrides the default."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("custom-threshold\n")
        try:
            _backdate(lock, seconds_ago=10)
            # Default 60s → fresh; custom 5s → stale.
            assert _stale_index_lock_age(git_project, threshold_s=60.0) is None
            result = _stale_index_lock_age(git_project, threshold_s=5.0)
            assert result is not None
            age, _ = result
            assert age >= 5.0
        finally:
            lock.unlink()

    def test_not_a_git_repo_returns_none(self, tmp_path: Path) -> None:
        """git rev-parse fails outside a repo → None (graceful)."""
        non_git = tmp_path / "not-git"
        non_git.mkdir()
        assert _stale_index_lock_age(non_git) is None

    def test_git_binary_missing_returns_none(self, git_project: Path) -> None:
        """FileNotFoundError on subprocess → None (graceful)."""
        import ai_hats.worktree as wt_mod

        def boom(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory: 'git'")

        with patch.object(wt_mod.subprocess, "run", side_effect=boom):
            assert _stale_index_lock_age(git_project) is None

    def test_linked_worktree_resolves_to_common_index_lock(
        self, git_project: Path
    ) -> None:
        """From inside a linked worktree, probe finds the common .git/index.lock.

        Linked worktrees don't have their own index.lock — git serializes
        all merges against the common dir's index.lock. The probe must
        walk up via `git rev-parse --git-common-dir`.
        """
        mgr = WorktreeManager(git_project, branch_name="task/hats-486-linked")
        wt_path = mgr.create()
        try:
            lock = git_project / ".git" / "index.lock"
            lock.write_text("from-linked\n")
            _backdate(lock, seconds_ago=STALE_INDEX_LOCK_THRESHOLD_S + 30)

            # Probe from INSIDE the linked worktree — must still find the
            # common .git/index.lock.
            result = _stale_index_lock_age(wt_path)
            assert result is not None
            _, path = result
            assert path.resolve() == lock.resolve()
        finally:
            lock_path = git_project / ".git" / "index.lock"
            if lock_path.exists():
                lock_path.unlink()
            mgr.discard(force=True)


# ---------------------------------------------------------------------------
# Integration through _retry_git_merge — probe on first retriable error
# ---------------------------------------------------------------------------


class TestRetryGitMergeStaleProbe:
    def test_warns_on_first_retriable_error_when_stale(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """First retriable error + stale lock → WARNING with hint."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("crashed\n")
        try:
            _backdate(lock, seconds_ago=STALE_INDEX_LOCK_THRESHOLD_S + 120)

            # Mock git runner: raises retriable error first attempt, then
            # succeeds. Tests both the probe AND that retry still works.
            attempts = []
            def mock_runner(*args):
                attempts.append(args)
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(
                        128, ["git", *args],
                        stderr="fatal: Another git process seems to be running\n",
                    )

            with caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
                _retry_git_merge(
                    mock_runner, "merge", "--no-ff", "task/foo",
                    sleep=lambda _: None,  # no real sleep in tests
                    project_dir=git_project,
                )

            warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING and "index.lock" in r.message
            ]
            assert warnings, (
                f"expected stale-lock WARNING; got: "
                f"{[(r.levelname, r.message) for r in caplog.records]}"
            )
            assert "rm -f" in warnings[0].message
            assert "HATS-486" in warnings[0].message
            assert len(attempts) == 2  # initial error + 1 retry success
        finally:
            if lock.exists():
                lock.unlink()

    def test_no_warn_when_lock_fresh(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Retriable error with FRESH lock (legit slow merge) → no probe WARN."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("fresh\n")  # mtime = now → under threshold
        try:
            attempts = []
            def mock_runner(*args):
                attempts.append(args)
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(
                        128, ["git", *args],
                        stderr="fatal: Another git process seems to be running\n",
                    )

            with caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
                _retry_git_merge(
                    mock_runner, "merge", "--no-ff", "task/foo",
                    sleep=lambda _: None,
                    project_dir=git_project,
                )

            stale_warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING and "stale" in r.message.lower()
                or "index.lock" in r.message and "rm -f" in r.message
            ]
            assert not stale_warnings, (
                f"unexpected stale-lock WARNING for fresh lock: {stale_warnings}"
            )
        finally:
            if lock.exists():
                lock.unlink()

    def test_no_probe_when_project_dir_none(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """project_dir=None (backwards-compat) → probe skipped entirely."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("crashed\n")
        try:
            _backdate(lock, seconds_ago=STALE_INDEX_LOCK_THRESHOLD_S + 30)

            attempts = []
            def mock_runner(*args):
                attempts.append(args)
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(
                        128, ["git", *args],
                        stderr="fatal: Another git process seems to be running\n",
                    )

            with caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
                _retry_git_merge(
                    mock_runner, "merge", "--no-ff", "task/foo",
                    sleep=lambda _: None,
                    # project_dir omitted — backwards-compat path.
                )

            stale_warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING and "index.lock" in r.message
            ]
            assert not stale_warnings, (
                f"probe ran despite project_dir=None: {stale_warnings}"
            )
        finally:
            if lock.exists():
                lock.unlink()

    def test_probe_only_on_first_attempt_no_spam(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Repeated retriable errors → probe runs only ONCE (no log spam)."""
        lock = git_project / ".git" / "index.lock"
        lock.write_text("crashed\n")
        try:
            _backdate(lock, seconds_ago=STALE_INDEX_LOCK_THRESHOLD_S + 30)

            # Always fail with retriable — exhausts MERGE_RETRY_MAX (8).
            def always_fail(*args):
                raise subprocess.CalledProcessError(
                    128, ["git", *args],
                    stderr="fatal: Another git process seems to be running\n",
                )

            with caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
                with pytest.raises(subprocess.CalledProcessError):
                    _retry_git_merge(
                        always_fail, "merge", "--no-ff", "task/foo",
                        sleep=lambda _: None,
                        project_dir=git_project,
                    )

            stale_warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING
                and "index.lock" in r.message and "rm -f" in r.message
            ]
            assert len(stale_warnings) == 1, (
                f"expected exactly 1 stale-lock WARNING across 8 retries, "
                f"got {len(stale_warnings)}: "
                f"{[r.message for r in stale_warnings]}"
            )
        finally:
            if lock.exists():
                lock.unlink()
