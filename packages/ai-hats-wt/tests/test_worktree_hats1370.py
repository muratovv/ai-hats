"""HATS-1370 unit tests for rebased branch refusal and containment."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats_wt import NOOP_LIFECYCLE, WorktreeManager, WorktreeRebasedBranchError

pytestmark = [pytest.mark.integration]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    project.mkdir()
    _git(project, "init", "-b", "master")
    (project / "README.md").write_text("initial")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "initial commit")
    return project


def test_rebased_branch_refuses_merge_without_accept_drift(bare_repo: Path, tmp_path: Path):
    state_dir = tmp_path / "state"
    mgr = WorktreeManager(
        bare_repo,
        branch_name="task/hats-1370-test",
        base_branch="master",
        lifecycle=NOOP_LIFECYCLE,
        state_dir=state_dir,
    )
    wt_path = mgr.create()
    mgr.save_state()

    # Make a commit in the worktree
    (wt_path / "feature.txt").write_text("feature content")
    _git(wt_path, "add", "feature.txt")
    _git(wt_path, "commit", "-m", "feature work")

    # Rebase/cherry-pick the commit onto master directly in main repo under a new SHA
    (bare_repo / "feature.txt").write_text("feature content")
    _git(bare_repo, "add", "feature.txt")
    _git(bare_repo, "commit", "-m", "feature work (rebased)")

    # Verify that mgr.merge() refuses without accept_drift
    with pytest.raises(WorktreeRebasedBranchError) as exc_info:
        mgr.merge(accept_drift=False)

    assert "task/hats-1370-test" in str(exc_info.value)
    assert "integrated into base 'master' under different SHAs" in str(exc_info.value)

    # Now verify that passing accept_drift=True allows teardown without duplicate merge
    mgr.merge(accept_drift=True)

    assert not wt_path.exists()
    assert WorktreeManager.branch_exists(bare_repo, "task/hats-1370-test") is False


def test_rebased_branch_detected_by_branch_merged_into_canonical_base(
    bare_repo: Path, tmp_path: Path
):
    state_dir = tmp_path / "state"
    mgr = WorktreeManager(
        bare_repo,
        branch_name="task/hats-1370-rebased",
        base_branch="master",
        lifecycle=NOOP_LIFECYCLE,
        state_dir=state_dir,
    )
    wt_path = mgr.create()
    mgr.save_state()

    (wt_path / "foo.txt").write_text("foo")
    _git(wt_path, "add", "foo.txt")
    _git(wt_path, "commit", "-m", "add foo")

    (bare_repo / "foo.txt").write_text("foo")
    _git(bare_repo, "add", "foo.txt")
    _git(bare_repo, "commit", "-m", "add foo rebased")

    base = WorktreeManager.branch_merged_into_canonical_base(
        bare_repo, "task/hats-1370-rebased", bases=("master",)
    )
    assert base == "master"

    # Remove worktree first so git allows branch deletion
    mgr._remove_worktree()

    deleted = WorktreeManager.delete_merged_branch(bare_repo, "task/hats-1370-rebased")
    assert deleted is True
