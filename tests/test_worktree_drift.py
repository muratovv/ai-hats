"""Drift detection on `wt merge` (HATS-457 / HYP-017).

Covers ``WorktreeManager._check_drift``: snapshot of original-branch
SHA at create time vs current local + ``origin/<base>`` SHA at merge
time. Failure surface = silent stale-baseline post-merge breakage
(HATS-361 incident).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_hats.paths import worktrees_dir
from ai_hats.worktree import (
    OriginalBranchMissingError,
    WorktreeBaseBranchMismatchError,
    WorktreeDriftError,
    WorktreeManager,
)


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
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def _commit_in_worktree(wt_path: Path, msg: str = "wt-work") -> None:
    """Make a commit on the worktree branch (otherwise merge is a no-op)."""
    _git(wt_path, "config", "user.email", "test@test.com")
    _git(wt_path, "config", "user.name", "Test")
    _git(wt_path, "commit", "--allow-empty", "-m", msg)


def _make_main_commit(project: Path, filename: str = "drift.txt") -> None:
    """Add a commit to the main branch in the primary worktree."""
    (project / filename).write_text("drift content")
    _git(project, "add", filename)
    _git(project, "commit", "-m", f"main: add {filename}")


class TestDriftDetection:
    """Pure local drift — no remote configured."""

    def test_no_drift_passes(self, git_project: Path) -> None:
        """Baseline: master unchanged between create and merge → merge succeeds."""
        mgr = WorktreeManager(git_project, branch_name="task/no-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        mgr.merge()  # no exception

        # Branch deleted on successful merge.
        listing = _git(git_project, "branch", "--list", "task/no-drift").stdout
        assert listing.strip() == ""

    def test_local_drift_raises(self, git_project: Path) -> None:
        """master advanced after create → merge refuses with drift message."""
        mgr = WorktreeManager(git_project, branch_name="task/local-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Simulate "another agent already merged into master".
        _make_main_commit(git_project, "other-agent.txt")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()

        msg = str(exc.value)
        assert "drifted" in msg
        assert "other-agent.txt" in msg
        assert "1 commit" in msg
        # HATS-509: the user-facing recipe ("re-run with
        # `ai-hats wt merge --accept-drift`") lives in CLI handlers
        # (cli/worktree.py wt_merge, cli/task.py task_transition), not
        # in the exception body. The body is facts-only so distinct
        # callers can phrase the recipe for their own surface.
        assert "--accept-drift" not in msg

        # Worktree branch is preserved on drift refusal — user can re-verify
        # and re-run with --accept-drift.
        listing = _git(git_project, "branch", "--list", "task/local-drift").stdout
        assert "task/local-drift" in listing

    def test_local_drift_accept_passes(self, git_project: Path) -> None:
        """--accept-drift bypasses the check; merge completes."""
        mgr = WorktreeManager(git_project, branch_name="task/accept-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)
        _make_main_commit(git_project, "other-agent.txt")

        mgr.merge(accept_drift=True)  # no exception

        listing = _git(git_project, "branch", "--list", "task/accept-drift").stdout
        assert listing.strip() == ""

    def test_force_does_not_bypass_drift(self, git_project: Path) -> None:
        """`force` is for dirty bypass only — drift remains blocked.

        Architectural decision (HATS-457): two checks address different
        risks, so they have different overrides. Mixing them would let
        a user who only meant "I know about uncommitted changes" silently
        merge stale work.
        """
        mgr = WorktreeManager(git_project, branch_name="task/force-not-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)
        _make_main_commit(git_project, "other.txt")

        with pytest.raises(WorktreeDriftError):
            mgr.merge(force=True)

    def test_drift_message_lists_multiple_paths(self, git_project: Path) -> None:
        mgr = WorktreeManager(git_project, branch_name="task/multi-paths")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        for name in ("a.txt", "b.txt", "c.txt"):
            (git_project / name).write_text(name)
        _git(git_project, "add", ".")
        _git(git_project, "commit", "-m", "main: a/b/c")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        msg = str(exc.value)
        assert "a.txt" in msg
        assert "b.txt" in msg
        assert "c.txt" in msg


class TestLegacyStateCompat:
    def test_legacy_state_no_field_skips_check(self, git_project: Path) -> None:
        """Pre-HATS-457 state files have no ``base_sha_at_create`` — skip."""
        mgr = WorktreeManager(git_project, branch_name="task/legacy")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Rewrite state file the way pre-457 code did — strip the new field.
        state_dir = worktrees_dir(git_project)
        state_file = state_dir / "task-legacy.json"
        data = json.loads(state_file.read_text())
        data.pop("base_sha_at_create", None)
        state_file.write_text(json.dumps(data, indent=2))

        # Reload via the public API — _base_sha_at_create stays None.
        reloaded = WorktreeManager.load_for_branch(git_project, "task/legacy")
        assert reloaded is not None
        assert reloaded._base_sha_at_create is None

        # Move master while worktree is "out". Pre-457 state → drift check
        # is a no-op, merge proceeds.
        _make_main_commit(git_project, "moved.txt")
        reloaded.merge()  # no exception

        listing = _git(git_project, "branch", "--list", "task/legacy").stdout
        assert listing.strip() == ""


class TestNoRemoteSwallowed:
    def test_no_remote_fetch_failure_swallowed(self, git_project: Path) -> None:
        """No ``origin`` remote → fetch fails silently, local check runs."""
        # git_project has no remote configured. Ensure local drift still works.
        mgr = WorktreeManager(git_project, branch_name="task/no-remote")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)
        _make_main_commit(git_project, "x.txt")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        # Message must describe local drift (no remote section).
        msg = str(exc.value)
        assert "local:" in msg
        assert "remote:" not in msg


class TestRemoteDrift:
    """Remote-only drift: local base unchanged, ``origin/<base>`` advanced.

    Scenario: agent created a worktree, did its work, but in the meantime
    a colleague pushed to ``origin/<base>``. Local ``<base>`` has not been
    pulled. The pre-merge ``fetch`` brings ``origin/<base>`` up to date,
    and the drift check must surface the remote divergence.
    """

    def _setup_origin(self, git_project: Path, tmp_path: Path) -> Path:
        """Create a bare ``origin`` clone and wire it up to git_project."""
        origin = tmp_path / "origin.git"
        # Clone bare so we can push to it without checked-out-branch headaches.
        subprocess.run(
            ["git", "clone", "--bare", str(git_project), str(origin)],
            capture_output=True, text=True, check=True,
        )
        _git(git_project, "remote", "add", "origin", str(origin))
        _git(git_project, "fetch", "origin")
        # Track upstream so `git rev-parse origin/<base>` resolves cleanly.
        head = _git(git_project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        _git(git_project, "branch", "--set-upstream-to", f"origin/{head}", head)
        return origin

    def test_remote_only_drift_raises(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """origin advanced while local base stayed → drift surfaces remote section."""
        origin = self._setup_origin(git_project, tmp_path)

        mgr = WorktreeManager(git_project, branch_name="task/remote-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Simulate a colleague pushing to origin without rebuilding our state:
        # check out the bare origin via a second working clone, commit, push.
        coworker = tmp_path / "coworker"
        subprocess.run(
            ["git", "clone", str(origin), str(coworker)],
            capture_output=True, text=True, check=True,
        )
        _git(coworker, "config", "user.email", "co@test")
        _git(coworker, "config", "user.name", "Co")
        (coworker / "remote-only.txt").write_text("from remote\n")
        _git(coworker, "add", "remote-only.txt")
        _git(coworker, "commit", "-m", "remote: add remote-only.txt")
        _git(coworker, "push", "origin", "HEAD")

        # `mgr.merge` runs `git fetch origin <base>` first → origin/<base>
        # advances past local; local stayed equal to base_sha_at_create.
        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        msg = str(exc.value)
        # Local section MUST NOT appear (base SHA unchanged locally).
        assert "local:" not in msg, f"unexpected local-drift section:\n{msg}"
        # Remote section MUST appear with the colleague's path.
        assert "remote:" in msg, f"remote-drift section missing:\n{msg}"
        assert "remote-only.txt" in msg, f"affected path missing:\n{msg}"
        # HATS-509: recipe lives in CLI layer, see TestLocalDrift comment.
        assert "--accept-drift" not in msg

    def test_local_and_remote_drift_both_listed(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """When both sources drift, the message must surface both sections."""
        origin = self._setup_origin(git_project, tmp_path)

        mgr = WorktreeManager(git_project, branch_name="task/both-drift")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Local drift.
        _make_main_commit(git_project, "local-drift.txt")

        # Remote drift (push via coworker clone, not pulled).
        coworker = tmp_path / "coworker"
        subprocess.run(
            ["git", "clone", str(origin), str(coworker)],
            capture_output=True, text=True, check=True,
        )
        _git(coworker, "config", "user.email", "co@test")
        _git(coworker, "config", "user.name", "Co")
        (coworker / "remote-drift.txt").write_text("from remote\n")
        _git(coworker, "add", "remote-drift.txt")
        _git(coworker, "commit", "-m", "remote: add remote-drift.txt")
        _git(coworker, "push", "origin", "HEAD")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        msg = str(exc.value)
        assert "local:" in msg
        assert "remote:" in msg
        assert "local-drift.txt" in msg
        assert "remote-drift.txt" in msg


class TestRichMarkupSafety:
    """Adversarial filenames must not inject Rich markup into the CLI output."""

    def test_filename_with_markup_chars_preserved_in_message(
        self, git_project: Path
    ) -> None:
        """Drift message keeps adversarial filename verbatim — CLI handler escapes."""
        mgr = WorktreeManager(git_project, branch_name="task/markup-safety")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Filename that would render as colored text if printed via Rich markup.
        # No `/` so it stays a single path segment.
        evil = "[red]boom-file.txt"
        (git_project / evil).write_text("nope\n")
        _git(git_project, "add", "--", evil)
        _git(git_project, "commit", "-m", "main: adversarial filename")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        # Message body keeps the filename verbatim (escaping happens at the
        # CLI render boundary, not in the exception payload).
        assert evil in str(exc.value)


class TestUnpushedLocalWorkNotDrift:
    """HATS-487: local-ahead-of-remote must NOT be flagged as remote drift.

    Pre-487 `_check_drift` flagged any ``current_remote != current_local``
    as "remote drift, 0 commits ahead of local" with a nonsense diff list
    of files going the WRONG direction (local→remote treated as
    remote→local). Caught live during HATS-482 transition done with
    10 unpushed commits on master.
    """

    def _setup_origin(self, git_project: Path, tmp_path: Path) -> Path:
        """Mirror of TestRemoteDrift._setup_origin — kept local so the two
        classes can evolve independently."""
        origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "clone", "--bare", str(git_project), str(origin)],
            capture_output=True, text=True, check=True,
        )
        _git(git_project, "remote", "add", "origin", str(origin))
        _git(git_project, "fetch", "origin")
        head = _git(git_project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        _git(git_project, "branch", "--set-upstream-to", f"origin/{head}", head)
        return origin

    def test_local_ahead_of_remote_is_not_drift(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """Local has unpushed commits + worktree merge → must NOT raise.

        Reproduces the HATS-482 incident exactly: origin is behind local,
        we create a worktree, do work, merge. Pre-487 raised
        WorktreeDriftError; post-487 succeeds.
        """
        self._setup_origin(git_project, tmp_path)

        # Push the worktree forward: create wt FIRST, then advance local
        # master past origin via a regular commit (mirrors "unpushed work
        # accumulated on master while a worktree was open").
        mgr = WorktreeManager(git_project, branch_name="task/unpushed-ok")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Unpushed local commits on master (NOT touched in wt).
        _make_main_commit(git_project, "unpushed-a.txt")
        _make_main_commit(git_project, "unpushed-b.txt")

        # `base_sha_at_create` matches the master SHA AT create time, so
        # local IS drifted (real local drift) — that's a SEPARATE
        # condition we still want to raise on. The HATS-487 contract is
        # narrower: the *remote* side must not trigger when remote is an
        # ancestor of local.
        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        msg = str(exc.value)
        # Local drift section IS expected (master moved post-create).
        assert "local:" in msg, f"expected local-drift section:\n{msg}"
        # Critical: NO remote-drift section (remote is ancestor of local).
        assert "remote:" not in msg, (
            f"HATS-487 regression: unpushed-local-work triggered a "
            f"phantom remote-drift section:\n{msg}"
        )

    def test_local_ahead_no_local_drift_merges_clean(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """Unpushed local work that happened BEFORE create → no drift at all.

        The base SHA captured at create-time IS the post-unpushed master,
        so local_drifted == False AND remote_drifted == False (remote is
        ancestor). Pre-487 still raised because of the simple
        ``current_remote != current_local`` check.
        """
        self._setup_origin(git_project, tmp_path)

        # Unpushed commits BEFORE creating the worktree.
        _make_main_commit(git_project, "unpushed-pre.txt")

        mgr = WorktreeManager(git_project, branch_name="task/unpushed-pre-create")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Should merge cleanly: local == base_sha_at_create, remote is
        # ancestor of local.
        mgr.merge()
        listing = _git(git_project, "branch", "--list", "task/unpushed-pre-create").stdout
        assert listing.strip() == "", "branch should be deleted on clean merge"

    def test_diverged_remote_and_local_is_drift(
        self, git_project: Path, tmp_path: Path
    ) -> None:
        """Local and remote both have unique commits → real drift.

        Mirror image of test_local_ahead: remote is NOT an ancestor of
        local (because both moved independently) → must raise.
        """
        origin = self._setup_origin(git_project, tmp_path)

        mgr = WorktreeManager(git_project, branch_name="task/diverged")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Local advances.
        _make_main_commit(git_project, "local-diverged.txt")

        # Remote advances via coworker (push commits origin didn't have).
        coworker = tmp_path / "coworker-diverged"
        subprocess.run(
            ["git", "clone", str(origin), str(coworker)],
            capture_output=True, text=True, check=True,
        )
        _git(coworker, "config", "user.email", "co@test")
        _git(coworker, "config", "user.name", "Co")
        (coworker / "remote-diverged.txt").write_text("from remote\n")
        _git(coworker, "add", "remote-diverged.txt")
        _git(coworker, "commit", "-m", "remote: diverged")
        _git(coworker, "push", "origin", "HEAD")

        with pytest.raises(WorktreeDriftError) as exc:
            mgr.merge()
        msg = str(exc.value)
        assert "local:" in msg, msg
        assert "remote:" in msg, msg
        assert "remote-diverged.txt" in msg, msg


class TestFetchFailureBehaviour:
    """HATS-489 / B-04 + B-05: fetch errors and missing git binary."""

    def test_fetch_failure_logs_warning_proceeds(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Fetch CalledProcessError → WARNING (not DEBUG) + merge proceeds.

        Pre-489 these were DEBUG-only — invisible to operators running at
        default log level, so a concurrent push that lands during merge
        would silently bypass remote-drift detection.
        """
        mgr = WorktreeManager(git_project, branch_name="task/fetch-warn")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # No 'origin' configured → real fetch would CalledProcessError.
        # Sanity-check that this is the path we're exercising by capturing
        # at WARNING level.
        with caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
            mgr.merge()  # must proceed (offline-merge contract)

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "fetch origin" in r.message
        ]
        assert warnings, (
            f"expected a fetch-failure WARNING; got records: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )
        # Worktree was deleted on successful merge → contract held.
        assert not wt_path.exists()

    def test_fetch_filenotfounderror_handled(
        self, git_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Git binary missing during fetch → no traceback, fallthrough.

        Pre-489 only CalledProcessError was caught — FileNotFoundError
        from a missing git would crash the merge with an unhandled
        exception. Now consistent with is_inside_linked_worktree /
        list_worktrees handlers.
        """
        mgr = WorktreeManager(git_project, branch_name="task/git-missing")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        real_git = mgr._git

        def selective_git(*args, **kwargs):
            # Only the fetch call raises FileNotFoundError; rev-parse and
            # the actual merge call go through real git.
            if args[:2] == ("fetch", "origin"):
                raise FileNotFoundError(2, "No such file or directory: 'git'")
            return real_git(*args, **kwargs)

        with patch.object(mgr, "_git", side_effect=selective_git), \
             caplog.at_level(logging.WARNING, logger="ai_hats.worktree"):
            mgr.merge()  # must not raise FileNotFoundError

        # Warning emitted for the simulated missing git.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("fetch origin" in r.message for r in warnings), (
            f"expected fetch-failure WARNING for FileNotFoundError; "
            f"records: {[(r.levelname, r.message) for r in caplog.records]}"
        )


class TestStateRoundtrip:
    def test_base_sha_persisted(self, git_project: Path) -> None:
        """``save_state`` writes ``base_sha_at_create``; load restores it."""
        mgr = WorktreeManager(git_project, branch_name="task/persist")
        mgr.create()
        mgr.save_state()

        state_file = worktrees_dir(git_project) / "task-persist.json"
        data = json.loads(state_file.read_text())
        assert "base_sha_at_create" in data
        assert data["base_sha_at_create"]
        assert len(data["base_sha_at_create"]) == 40  # full SHA

        reloaded = WorktreeManager.load_for_branch(git_project, "task/persist")
        assert reloaded is not None
        assert reloaded._base_sha_at_create == data["base_sha_at_create"]


class TestBaseBranchMismatch:
    """HATS-533: refuse ``merge()`` when main-repo HEAD wandered off
    ``_original_branch`` between create and merge.

    Semantic peer of :class:`TestDriftDetection` — both test the gap
    between "what worktree captured at create time" and "what main repo
    looks like at merge time", but along different axes:

    * Drift = base branch tip moved (someone merged into it).
    * Mismatch = main-repo HEAD itself moved to a different branch.

    Both are silent-wrong-branch-merge precursors (HATS-486 class).
    """

    def test_head_wandered_raises(self, git_project: Path) -> None:
        """master at create → checkout different branch → merge refuses."""
        mgr = WorktreeManager(git_project, branch_name="task/head-wandered")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        original = mgr._original_branch
        assert original is not None  # sanity

        # Simulate "HEAD moved to a feature branch between create and merge"
        # — e.g. operator manually checked out, or a peer agent committed
        # directly in main repo without using a linked worktree.
        _git(git_project, "checkout", "-b", "wandered-feature")

        with pytest.raises(WorktreeBaseBranchMismatchError) as exc:
            mgr.merge()

        assert exc.value.current == "wandered-feature"
        assert exc.value.expected == original

        # Critical safety assertion: no merge happened on EITHER branch.
        # `wandered-feature` MUST NOT carry the worktree commit (that would
        # be the silent-wrong-branch-merge we're guarding against). And
        # `original` (master) MUST be unchanged.
        wandered_log = _git(
            git_project, "log", "--oneline", "wandered-feature"
        ).stdout
        assert "wt-work" not in wandered_log, (
            f"wandered branch must not have received the worktree commit:\n"
            f"{wandered_log}"
        )
        original_log = _git(git_project, "log", "--oneline", original).stdout
        assert "wt-work" not in original_log, (
            f"original branch must be unchanged after refusal:\n{original_log}"
        )

        # Worktree branch preserved — refusal is recoverable by
        # `git checkout <original>` + retry.
        listing = _git(
            git_project, "branch", "--list", "task/head-wandered"
        ).stdout
        assert "task/head-wandered" in listing

    def test_head_on_original_branch_passes(self, git_project: Path) -> None:
        """Inverse: when HEAD matches `_original_branch`, merge proceeds.

        Pinning the happy path against accidental over-restriction in the
        guard (e.g. comparing against the wrong field).
        """
        mgr = WorktreeManager(git_project, branch_name="task/matched-head")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # HEAD already on `_original_branch` (default for the fixture).
        mgr.merge()  # no exception

        listing = _git(
            git_project, "branch", "--list", "task/matched-head"
        ).stdout
        assert listing.strip() == "", "branch should be cleaned up on merge"

    def test_legacy_state_no_original_branch_skips_guard(
        self, git_project: Path
    ) -> None:
        """Legacy state with ``_original_branch=None`` bypasses the guard.

        Symmetric to the existing ``OriginalBranchMissingError`` short-
        circuit at worktree.py:1189 (``if self._original_branch and not
        self._branch_exists(...)``). A legacy state JSON without the
        ``original_branch`` field must not crash with mismatch — it falls
        through to whatever the merge naturally does, preserving the
        pre-HATS-533 behavior for migration paths.
        """
        mgr = WorktreeManager(git_project, branch_name="task/legacy-state")
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Hand-clear the field to simulate legacy state.
        mgr._original_branch = None

        # Move HEAD off the original branch to ensure we'd trip the guard
        # if it ran.
        _git(git_project, "checkout", "-b", "any-feature")

        # No WorktreeBaseBranchMismatchError. Whether the merge then
        # succeeds or fails downstream is not this test's concern — we
        # only assert that the new guard does NOT fire for legacy state.
        # Narrow `except` list (no bare Exception): each clause is an
        # explicitly-acceptable downstream consequence of having
        # `_original_branch=None`. An UNEXPECTED type — e.g. an
        # AttributeError from a future guard regression that drops the
        # `is not None` check — IS the regression this test catches:
        # such an exception falls through, pytest reports an ERROR, and
        # the contract violation is visible.
        try:
            mgr.merge()
        except WorktreeBaseBranchMismatchError:
            pytest.fail(
                "mismatch guard fired for legacy state "
                "(_original_branch=None) — must be a no-op"
            )
        except OriginalBranchMissingError:
            # Expected: legacy state path resolves `_original_branch` as
            # None / missing; the existing `_branch_exists` check raises
            # this. Pre-HATS-533 behavior preserved.
            pass
        except (subprocess.CalledProcessError, TypeError):
            # Expected downstream from `_check_drift` (worktree.py)
            # invoking ``self._git("rev-parse", None)`` — TypeError from
            # subprocess args validation, OR CalledProcessError if git
            # is reached. Both confirm only that the GUARD itself
            # short-circuited; downstream merge mechanics on legacy
            # state were never made to work end-to-end. Out of scope
            # for HATS-533, separate concern.
            pass

    def test_force_does_not_bypass_mismatch(self, git_project: Path) -> None:
        """``--force`` (uncommitted-bypass) does NOT bypass the HEAD guard.

        Same architectural decision as drift (HATS-457): ``force``
        addresses dirty-worktree, not wrong-branch safety. Mixing them
        would let an operator who only meant "I know about uncommitted
        changes" silently merge to the wrong branch.
        """
        mgr = WorktreeManager(
            git_project, branch_name="task/force-not-mismatch"
        )
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)
        _git(git_project, "checkout", "-b", "another-feature")

        with pytest.raises(WorktreeBaseBranchMismatchError):
            mgr.merge(force=True)

    def test_accept_drift_does_not_bypass_mismatch(
        self, git_project: Path
    ) -> None:
        """``--accept-drift`` does NOT bypass the HEAD guard either.

        ``accept-drift`` is a deliberate consent to a moved base; it
        doesn't grant consent to merge into a DIFFERENT branch entirely.
        Mismatch guard runs before drift check (worktree.py order) and is
        not gated by ``accept_drift``.
        """
        mgr = WorktreeManager(
            git_project, branch_name="task/accept-not-mismatch"
        )
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)
        _git(git_project, "checkout", "-b", "yet-another-feature")

        with pytest.raises(WorktreeBaseBranchMismatchError):
            mgr.merge(accept_drift=True)

    def test_mismatch_wins_over_dirty_worktree(
        self, git_project: Path
    ) -> None:
        """Mismatch surfaces BEFORE the dirty-worktree error.

        Pins the documented ordering invariant (worktree.py: HATS-533
        guard runs ahead of ``_check_clean``). Without this test, a
        future re-ordering that surfaces ``WorktreeDirtyError`` first
        would slip through — confusing the operator about the actual
        root cause (HEAD wandering, not their uncommitted edit).
        """
        mgr = WorktreeManager(
            git_project, branch_name="task/dirty-and-wandered"
        )
        wt_path = mgr.create()
        mgr.save_state()
        _commit_in_worktree(wt_path)

        # Make the worktree dirty (would normally trip WorktreeDirtyError).
        (wt_path / "uncommitted.txt").write_text("dirty\n")

        # And move main-repo HEAD off the merge target.
        _git(git_project, "checkout", "-b", "wandered-too")

        # Mismatch wins.
        with pytest.raises(WorktreeBaseBranchMismatchError):
            mgr.merge()
