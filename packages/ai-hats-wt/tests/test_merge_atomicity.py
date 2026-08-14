"""HATS-1651 — a refused merge leaves the main checkout exactly as it found it.

Real git throughout: the subject is what `git merge` does to a checkout when it
stops halfway, which no double can tell us.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats_wt import (
    NOOP_LIFECYCLE,
    WorktreeManager,
    WorktreeMainRepoMidMergeError,
    WorktreeMergeConflictError,
)

pytestmark = [pytest.mark.integration]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True
    )


def _mid_merge(repo: Path) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _unmerged(repo: Path) -> list[str]:
    out = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout
    return [line for line in out.splitlines() if line.strip()]


@pytest.fixture
def conflict(tmp_path: Path) -> tuple[WorktreeManager, Path, Path]:
    """A repo where merging the worktree branch into master must conflict.

    Both sides rewrite the same line of the same tracked file — the only shape
    that makes git start the merge and then stop in the middle of it.
    """
    project = tmp_path / "repo"
    project.mkdir()
    _git(project, "init", "-b", "master")
    (project / "CONFLICT.txt").write_text("v1\n")
    _git(project, "add", "CONFLICT.txt")
    _git(project, "commit", "-m", "initial commit")

    mgr = WorktreeManager(
        project,
        branch_name="task/conflict",
        base_branch="master",
        lifecycle=NOOP_LIFECYCLE,
        state_dir=tmp_path / "state",
    )
    wt_path = mgr.create()
    mgr.save_state()

    (wt_path / "CONFLICT.txt").write_text("from-worktree\n")
    _git(wt_path, "add", "CONFLICT.txt")
    _git(wt_path, "commit", "-m", "worktree rewrites the line")

    (project / "CONFLICT.txt").write_text("from-master\n")
    _git(project, "add", "CONFLICT.txt")
    _git(project, "commit", "-m", "master rewrites the line")
    return mgr, project, wt_path


@pytest.mark.parametrize("squash", [False, True], ids=["no-ff", "squash"])
def test_a_conflict_is_refused_with_the_main_checkout_put_back(conflict, squash: bool):
    """Both merge paths, because they fail differently.

    ``--no-ff`` writes MERGE_HEAD and ``git merge --abort`` undoes it; ``--squash``
    writes none, so nothing downstream would even detect the leftover index — the
    reason this parametrisation is not redundant.
    """
    mgr, project, wt_path = conflict
    head_before = _git(project, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(WorktreeMergeConflictError) as raised:
        mgr.merge(squash=squash, accept_drift=True)

    assert raised.value.paths == ("CONFLICT.txt",), (
        f"the refusal must name what conflicted, got {raised.value.paths}"
    )
    assert not _mid_merge(project), "MERGE_HEAD survived a refusal that claims a rollback"
    assert not _unmerged(project), "the index still holds an unresolved merge"
    assert _git(project, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (project / "CONFLICT.txt").read_text() == "from-master\n", (
        "the rollback left conflict markers in the main checkout"
    )
    # HATS-587 contract: a failed merge tears nothing down.
    assert wt_path.is_dir()
    assert "task/conflict" in _git(project, "branch", "--list", "task/conflict").stdout


def test_the_retry_gets_the_same_refusal_not_a_mid_merge_complaint(conflict):
    """Idempotency: the observable defect was the SECOND run's answer.

    Before this card the first run left MERGE_HEAD behind, so the retry refused
    with :class:`WorktreeMainRepoMidMergeError` — a demand to clean up after an
    operation the first run reported as having changed nothing.
    """
    mgr, _project, _wt = conflict

    with pytest.raises(WorktreeMergeConflictError) as first:
        mgr.merge(accept_drift=True)
    with pytest.raises(WorktreeMergeConflictError) as second:
        mgr.merge(accept_drift=True)

    assert str(second.value) == str(first.value)
    assert not isinstance(second.value, WorktreeMainRepoMidMergeError)


def test_uncommitted_work_in_the_main_checkout_survives_the_rollback(conflict):
    """The rollback must never be a ``reset --hard``.

    ``wt merge`` runs against a main checkout that is routinely dirty — the
    operator's own edits on the CLI road, rack's tracker writes on the FSM one.
    Tidying up a refusal by destroying that would trade this card's bug for a
    worse one, so an unrelated dirty file is part of the contract.
    """
    mgr, project, _wt = conflict
    (project / "SCRATCH.txt").write_text("work in progress\n")

    with pytest.raises(WorktreeMergeConflictError):
        mgr.merge(accept_drift=True)

    assert (project / "SCRATCH.txt").read_text() == "work in progress\n", (
        "the merge rollback destroyed uncommitted work in the main checkout"
    )
