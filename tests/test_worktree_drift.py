"""Drift detection on `wt merge` (HATS-457 / HYP-017).

Covers ``WorktreeManager._check_drift``: snapshot of original-branch
SHA at create time vs current local + ``origin/<base>`` SHA at merge
time. Failure surface = silent stale-baseline post-merge breakage
(HATS-361 incident).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_hats.paths import worktrees_dir
from ai_hats.worktree import WorktreeDriftError, WorktreeManager


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
        assert "--accept-drift" in msg

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
        assert "--accept-drift" in msg

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
