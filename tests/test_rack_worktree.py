"""Ported groups 5–7 (incidents §4) on the rack worktree adapter, real git:

* group 5 — HATS-697/PROX-287: already-merged/state-lost finalize, genuinely
  un-merged refusal, forced execute spins no worktree, both-gone silent;
* group 6 — HATS-788: teardown refused from inside the tree being removed;
* group 7 — HATS-518/596: canonical-base guard force-proof, ``done --force``
  forwards into merge, HEAD-wandered already-merged finalize;

plus the PROP pins new to K3: pre-destroy event before destructive teardown
(PROP-047), cancelled preserves uncommitted work (PROP-084), repo-aware
done-guard (PROP-056/057), and the HATS-979/818 pending-hunk-review reclaim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats_rack import OperationAborted
from ai_hats_rack.dispatch import AbortOperation, Phase, Subscription
from ai_hats.paths import worktrees_dir
from ai_hats.rack_wiring import build_rack_kernel
from ai_hats_wt import WorktreeBaseBranchError, WorktreeManager, WorktreeStateLostError

pytestmark = pytest.mark.integration

_FILLED_PLAN = (
    "# Plan\n\n## Requirements\nrack.\n\n## Scope & Out-of-scope\nin/out\n\n"
    "## Steps\n- [ ] do\n\n## Verification Protocol\npytest\n"
)


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    """Ownership stays inert here — its behaviour is pinned in test_rack_ownership."""
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, test helper
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def _init_git(path: Path) -> None:
    _git(path, "init", "-b", "master")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("# test")
    _git(path, "add", ".")
    _git(path, "-c", "commit.gpgsign=false", "commit", "-m", "init")


def _commit_all(cwd: Path, message: str) -> None:
    _git(cwd, "add", ".")
    _git(cwd, "-c", "commit.gpgsign=false", "commit", "-m", message)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    _init_git(p)
    (p / ".agent").mkdir()
    return p


def _kernel(project: Path, **kwargs):
    return build_rack_kernel(
        project,
        tasks_dir=project / ".agent" / "tasks",
        state_md_path=project / ".agent" / "STATE.md",
        prefix="T",
        **kwargs,
    )


def _fill_plan(kernel, task_id: str) -> None:
    (kernel.tasks_dir / task_id / "plan.md").write_text(_FILLED_PLAN)


def _to_execute(kernel, cwd: Path, task_id: str = "T-1", title: str = "t") -> str:
    kernel.create(actor="test", caller_cwd=cwd, task_id=task_id, title=title)
    kernel.transition(task_id, "plan", actor="test", caller_cwd=cwd)
    _fill_plan(kernel, task_id)
    kernel.transition(task_id, "execute", actor="test", caller_cwd=cwd)
    return task_id


def _tr(kernel, task_id: str, *states: str, cwd: Path, **kwargs):
    for state in states:
        result = kernel.transition(task_id, state, actor="test", caller_cwd=cwd, **kwargs)
    return result


def _active(project: Path, task_id: str):
    return WorktreeManager.load_for_task(project, task_id, state_dir=worktrees_dir(project))


# ---------------------------------------------------------------------------
# Baseline lifecycle on real git (PROP-051/052 anchor point; HATS-866 heirs)
# ---------------------------------------------------------------------------


def test_execute_creates_worktree(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    active = _active(project, "T-1")
    assert active is not None
    assert active.branch_name == "task/t-1"
    assert active.worktree_path.exists()
    logs = [e.message for e in kernel.get("T-1").work_log]
    assert any(f"Worktree: {active.worktree_path}" in m for m in logs)  # HATS-866/AC5


def test_done_merges_worktree(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "new_file.txt").write_text("hello")
    _commit_all(wt, "add file")

    _tr(kernel, "T-1", "document", "review", "done", cwd=project)

    assert _active(project, "T-1") is None  # cleaned up
    assert (project / "new_file.txt").exists()  # merged into main
    assert any("Worktree merged" in e.message for e in kernel.get("T-1").work_log)


def test_failed_discards_worktree(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    _tr(kernel, "T-1", "failed", cwd=project)
    assert _active(project, "T-1") is None
    assert not wt.exists()


def test_cancel_discards_committed_work(project):
    """Committed-but-unmerged work is dropped on cancel (admin close)."""
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "junk.txt").write_text("should not survive")
    _commit_all(wt, "wip junk")

    _tr(kernel, "T-1", "cancelled", cwd=project, resolution="wont-fix")

    assert _active(project, "T-1") is None
    assert not wt.exists()
    assert not (project / "junk.txt").exists()  # NOT merged
    assert kernel.get("T-1").state == "cancelled"


def test_cancel_preserves_uncommitted_work(project):
    """PROP-084: a destructive terminal warns and keeps uncommitted work
    instead of silently eating it."""
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "draft.txt").write_text("uncommitted draft")  # dirty tree

    _tr(kernel, "T-1", "cancelled", cwd=project, resolution="wont-fix")

    assert kernel.get("T-1").state == "cancelled"  # the close itself proceeds
    assert wt.exists() and (wt / "draft.txt").exists()  # work preserved
    logs = [e.message for e in kernel.get("T-1").work_log]
    assert any("Worktree preserved: uncommitted changes" in m for m in logs)


def test_execute_reuses_worktree_after_blocked(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    _tr(kernel, "T-1", "blocked", cwd=project)
    assert _active(project, "T-1") is not None  # survives blocked
    _tr(kernel, "T-1", "execute", cwd=project)
    assert _active(project, "T-1").worktree_path == wt  # same tree adopted


def test_review_to_execute_reworks_without_merge(project):
    """HATS-1052: the rework loop-back must NOT merge (unlike review → done) and
    the task worktree survives for the rework. Subscriber-level proof: no
    pre-destroy(worktree-merge) fires, the tree is reused, and the first-pass
    work never leaks into the base."""
    probe = _PreDestroyProbe()
    kernel = _kernel(project, extra_subscribers=[probe])
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "first_pass.txt").write_text("draft under review")
    _commit_all(wt, "first pass")
    _tr(kernel, "T-1", "document", "review", cwd=project)

    _tr(kernel, "T-1", "execute", cwd=project)  # review returned WITH comments

    assert kernel.get("T-1").state == "execute"
    assert probe.seen == []  # no teardown/merge machinery ran
    active = _active(project, "T-1")
    assert active is not None and active.worktree_path == wt  # same tree survives
    assert wt.exists()
    logs = [e.message for e in kernel.get("T-1").work_log]
    assert not any("merged" in m.lower() for m in logs)  # nothing was merged
    assert not (project / "first_pass.txt").exists()  # base untouched
    # the surviving tree still accepts the rework commits
    (wt / "addressed.txt").write_text("comments addressed")
    _commit_all(wt, "address review comments")


def test_no_worktree_in_non_git_project(tmp_path):
    plain = tmp_path / "plain"
    (plain / ".agent").mkdir(parents=True)
    kernel = _kernel(plain)
    _to_execute(kernel, plain)
    assert _active(plain, "T-1") is None
    assert kernel.get("T-1").state == "execute"


def test_epic_execute_no_worktree_but_childless_still_gets_one(project):
    """HATS-794: an epic entering execute is a pure flip; a childless task on
    the same path still gets its worktree (regression guard)."""
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="Epic")
    kernel.create(
        actor="test", caller_cwd=project, task_id="T-2", title="Child", parent_task="T-1"
    )
    _tr(kernel, "T-1", "plan", "execute", cwd=project)  # empty plan: epic is not gated
    assert kernel.get("T-1").state == "execute"
    assert _active(project, "T-1") is None  # epics never get a worktree

    _to_execute(kernel, project, "T-3", "Solo")
    assert _active(project, "T-3") is not None


def test_epicify_reclaims_clean_worktree_but_keeps_pending_hunk_review(project):
    """HATS-979/818 full stack: epicify reclaims the parent's clean worktree,
    but a gitignored pending ``.hunk/notes.json`` holds it until drained."""
    (project / ".gitignore").write_text(".hunk/\n")
    _commit_all(project, "ignore .hunk")

    kernel = _kernel(project)
    _to_execute(kernel, project, "T-1", "Parent")
    wt = _active(project, "T-1").worktree_path
    (wt / ".hunk").mkdir(parents=True)
    (wt / ".hunk" / "notes.json").write_text('[{"id": "u:1", "text": "review me"}]')

    kernel.create(actor="test", caller_cwd=project, task_id="T-2", title="C", parent_task="T-1")
    assert wt.exists(), "worktree with pending hunk review must NOT be reclaimed"

    (wt / ".hunk" / "notes.json").write_text("[]")  # review drained
    kernel.create(actor="test", caller_cwd=project, task_id="T-3", title="C2", parent_task="T-1")
    assert not wt.exists(), "drained, git-clean worktree must be reclaimed at epicify"
    assert _active(project, "T-1") is None


# ---------------------------------------------------------------------------
# Group 5 — HATS-697 / PROX-287: git is the truth
# ---------------------------------------------------------------------------


def test_forced_execute_no_worktree(project):
    """HATS-697: a forced execute is a manual state correction — no worktree,
    no task branch (spinning one off HEAD orphaned retro work, PROX-287)."""
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    _tr(kernel, "T-1", "plan", cwd=project)
    _fill_plan(kernel, "T-1")

    _tr(kernel, "T-1", "execute", cwd=project, force=True, reason="shipped on master")

    assert kernel.get("T-1").state == "execute"
    assert _active(project, "T-1") is None
    assert not WorktreeManager.branch_exists(project, "task/t-1")
    logs = [e.message for e in kernel.get("T-1").work_log]
    assert any("no worktree created" in m for m in logs)


def test_state_lost_branch_unmerged_refuses(project, monkeypatch):
    """HATS-541/697: state JSON gone + branch with genuinely un-merged commits
    → typed ``WorktreeStateLostError``; the card never silently reaches done."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "unmerged.txt").write_text("un-merged work\n")
    _commit_all(wt, "un-merged worktree work")
    _tr(kernel, "T-1", "document", "review", cwd=project)

    monkeypatch.setattr(
        worktree_module.WorktreeManager, "load_for_task", staticmethod(lambda *a, **kw: None)
    )
    with pytest.raises(WorktreeStateLostError) as exc_info:
        _tr(kernel, "T-1", "done", cwd=project)
    assert exc_info.value.task_id == "T-1"
    assert exc_info.value.branch_name == "task/t-1"

    reloaded = kernel.get("T-1")
    assert reloaded.state == "review"
    assert reloaded.completed_at == ""


def test_state_lost_branch_merged_finalizes(project, monkeypatch):
    """HATS-697/PROX-287: work merged out-of-band + worktree removed by hand →
    finalize without re-merge instead of a false state-lost refusal."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "shipped.txt").write_text("shipped on master\n")
    _commit_all(wt, "shipped work")
    _git(project, "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", "task/t-1")
    base_sha = _git(project, "rev-parse", "master").stdout.strip()

    _tr(kernel, "T-1", "document", "review", cwd=project)
    _git(project, "worktree", "remove", "--force", str(wt))
    monkeypatch.setattr(
        worktree_module.WorktreeManager, "load_for_task", staticmethod(lambda *a, **kw: None)
    )

    _tr(kernel, "T-1", "done", cwd=project)  # must NOT raise

    reloaded = kernel.get("T-1")
    assert reloaded.state == "done"
    assert reloaded.completed_at != ""
    assert not WorktreeManager.branch_exists(project, "task/t-1")  # branch cleaned up
    assert _git(project, "rev-parse", "master").stdout.strip() == base_sha  # no double merge


def test_state_and_branch_both_gone_silent_done(project, monkeypatch):
    """HATS-541 carve-out: nothing to recover → done proceeds silently."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", cwd=project)
    active = _active(project, "T-1")
    active.discard(force=True)  # removes dir, deletes branch, clears state
    assert not WorktreeManager.branch_exists(project, "task/t-1")

    monkeypatch.setattr(
        worktree_module.WorktreeManager, "load_for_task", staticmethod(lambda *a, **kw: None)
    )
    _tr(kernel, "T-1", "done", cwd=project)
    assert kernel.get("T-1").state == "done"


def test_discard_path_silent_when_state_lost(project, monkeypatch):
    """HATS-541 carve-out: the discard path (failed) keeps silent return even
    when the branch still exists — admin closes are intentionally lossy."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)  # creates task/t-1
    monkeypatch.setattr(
        worktree_module.WorktreeManager, "load_for_task", staticmethod(lambda *a, **kw: None)
    )
    _tr(kernel, "T-1", "failed", cwd=project)  # must not raise
    assert kernel.get("T-1").state == "failed"


# ---------------------------------------------------------------------------
# Group 7 — HATS-518 / HATS-596: force ≠ safety
# ---------------------------------------------------------------------------


def test_execute_refused_on_feature_branch(project):
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    _tr(kernel, "T-1", "plan", cwd=project)
    _fill_plan(kernel, "T-1")
    _git(project, "checkout", "-b", "feat/parking")

    with pytest.raises(WorktreeBaseBranchError):
        _tr(kernel, "T-1", "execute", cwd=project)
    assert kernel.get("T-1").state == "plan"  # nothing persisted


def test_execute_refused_even_with_force(project):
    """HATS-518: force overrides the FSM arrow, NOT the canonical-base safety
    contract — the forced path must run the guard explicitly."""
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    _tr(kernel, "T-1", "plan", cwd=project)
    _fill_plan(kernel, "T-1")
    _git(project, "checkout", "-b", "feat/parking")

    with pytest.raises(WorktreeBaseBranchError):
        _tr(kernel, "T-1", "execute", cwd=project, force=True, reason="bypass attempt")
    assert kernel.get("T-1").state == "plan"


class _SpyMergeManager:
    branch_name = "task/t-1"
    worktree_path = Path("/nonexistent-spy-worktree")

    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def merge(self, *, force: bool = False) -> None:
        self._captured["force"] = force

    def discard(self, *, force: bool = False) -> None:  # pragma: no cover
        pass


def test_done_force_forwards_to_merge(project, monkeypatch):
    """HATS-596: ``done --force`` must reach ``merge(force=True)`` so the
    clean-tree gate can be overridden (and ONLY that gate)."""
    import ai_hats_wt as worktree_module

    captured: dict = {}
    kernel = _kernel(project)
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", cwd=project)
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *a, **kw: _SpyMergeManager(captured)),
    )
    _tr(kernel, "T-1", "done", cwd=project, force=True, reason="corrective finalize")
    assert captured.get("force") is True


class _FailingMergeManager:
    branch_name = "task/t-1"
    worktree_path = Path("/nonexistent-failing-worktree")

    def merge(self, *, force: bool = False) -> None:
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "merge", "--no-ff", self.branch_name],
            stderr="fatal: Unable to create '.git/index.lock': File exists.",
        )

    def discard(self, *, force: bool = False) -> None:
        raise subprocess.CalledProcessError(returncode=128, cmd=["git", "branch", "-D"])


def test_merge_failure_leaves_review(project, monkeypatch):
    """TC-N13 (HATS-481): a merge failure on ``done`` must leave the card in
    review — the silent-data-loss class."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", cwd=project)
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *a, **kw: _FailingMergeManager()),
    )
    with pytest.raises(subprocess.CalledProcessError):
        _tr(kernel, "T-1", "done", cwd=project)

    reloaded = kernel.get("T-1")
    assert reloaded.state == "review", "task moved despite merge failure — silent data loss"
    assert reloaded.completed_at == ""


def test_discard_failure_swallowed_on_cancelled(project, monkeypatch):
    """Admin close stays permissive: a failing discard never blocks cancel."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-2", title="t")
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *a, **kw: _FailingMergeManager()),
    )
    _tr(kernel, "T-2", "cancelled", cwd=project, resolution="dropped")  # no raise
    assert kernel.get("T-2").state == "cancelled"


def test_head_wandered_already_merged_finalizes(project, monkeypatch):
    """HATS-596/697: the done-guard reads branch ancestry, not the HEAD of the
    main checkout — a wandered HEAD must not produce a false refusal."""
    import ai_hats_wt as worktree_module

    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    (wt / "done.txt").write_text("work\n")
    _commit_all(wt, "work")
    _git(project, "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", "task/t-1")
    _tr(kernel, "T-1", "document", "review", cwd=project)

    _git(project, "worktree", "remove", "--force", str(wt))
    _git(project, "checkout", "-b", "feat/elsewhere")  # HEAD wanders off master
    monkeypatch.setattr(
        worktree_module.WorktreeManager, "load_for_task", staticmethod(lambda *a, **kw: None)
    )

    _tr(kernel, "T-1", "done", cwd=project)
    assert kernel.get("T-1").state == "done"


# ---------------------------------------------------------------------------
# Group 6 — HATS-788: never destroy the tree the caller stands in
# ---------------------------------------------------------------------------


def test_done_from_inside_worktree_refused(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    _tr(kernel, "T-1", "document", "review", cwd=project)

    with pytest.raises(OperationAborted) as exc_info:
        _tr(kernel, "T-1", "done", cwd=wt)  # caller stands inside the tree
    assert "linked worktree" in exc_info.value.reason
    assert wt.is_dir(), "refused close must not remove the worktree"
    assert kernel.get("T-1").state == "review"


def test_cancel_from_inside_worktree_refused(project):
    kernel = _kernel(project)
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path

    with pytest.raises(OperationAborted):
        _tr(kernel, "T-1", "cancelled", cwd=wt / "sub" / "dir", resolution="drop")
    assert wt.is_dir()
    assert kernel.get("T-1").state == "execute"


def test_execute_adopts_callers_worktree(project):
    """HATS-060/840: execute issued from inside a linked worktree adopts it —
    no nested task worktree is spun."""
    wt_mgr = WorktreeManager(project, branch_name="feat/t-9-foo")
    wt_path = wt_mgr.create()
    wt_mgr.save_state()

    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-9", title="t")
    _tr(kernel, "T-9", "plan", cwd=project)
    _fill_plan(kernel, "T-9")

    _tr(kernel, "T-9", "execute", cwd=wt_path)  # operator's shell is inside the wt

    branches = {w.get("branch", "") for w in WorktreeManager.list_worktrees(project)}
    assert not any(b.endswith("/task/t-9") for b in branches), f"nested worktree: {branches}"
    logs = [e.message for e in kernel.get("T-9").work_log]
    assert any(f"Worktree: {wt_path}" in m for m in logs)


# ---------------------------------------------------------------------------
# PROP-047 — pre-destroy event before irreversible teardown
# ---------------------------------------------------------------------------


class _PreDestroyProbe:
    """Named consumer of ``pre-destroy``: records (or blocks) destruction."""

    name = "pre-destroy-probe"

    def __init__(self, abort: bool = False) -> None:
        self._abort = abort
        self.seen: list[tuple[str, str, bool]] = []

    def subscriptions(self):
        return [Subscription("pre-destroy", Phase.IN_LOCK, 10)]

    def on_event(self, ctx):
        wt_alive = _active(Path(ctx.caller_cwd), ctx.event.task_id) is not None
        self.seen.append((ctx.event.operation, ctx.event.task_id, wt_alive))
        if self._abort:
            raise AbortOperation("review notes not drained — extract before merge")
        return None


def test_pre_destroy_published_before_merge(project):
    probe = _PreDestroyProbe()
    kernel = _kernel(project, extra_subscribers=[probe])
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", "done", cwd=project)

    assert probe.seen == [("worktree-merge", "T-1", True)]  # fired while the tree existed


def test_pre_destroy_abort_cancels_teardown(project):
    probe = _PreDestroyProbe(abort=True)
    kernel = _kernel(project, extra_subscribers=[probe])
    _to_execute(kernel, project)
    wt = _active(project, "T-1").worktree_path
    _tr(kernel, "T-1", "document", "review", cwd=project)

    with pytest.raises(OperationAborted) as exc_info:
        _tr(kernel, "T-1", "done", cwd=project)
    assert "review notes" in exc_info.value.reason
    assert wt.exists(), "aborted pre-destroy must leave the worktree intact"
    assert kernel.get("T-1").state == "review"


def test_pre_destroy_published_before_discard(project):
    probe = _PreDestroyProbe()
    kernel = _kernel(project, extra_subscribers=[probe])
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "failed", cwd=project)
    assert probe.seen == [("worktree-discard", "T-1", True)]


# ---------------------------------------------------------------------------
# PROP-056/057 — repo-aware done-guard
# ---------------------------------------------------------------------------


def _second_repo(tmp_path: Path, task_branch: str, *, merged: bool) -> Path:
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    _init_git(repo)
    _git(repo, "checkout", "-b", task_branch)
    (repo / "deliverable.txt").write_text("consumer work\n")
    _commit_all(repo, "deliverable")
    _git(repo, "checkout", "master")
    if merged:
        _git(repo, "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", task_branch)
    return repo


def _set_task_repo(kernel, task_id: str, repo: Path) -> None:
    card = kernel.get(task_id)
    card.extras["repo"] = str(repo)
    card.save(kernel.tasks_dir / task_id / "task.yaml")


def test_repo_aware_done_guard_refuses_unmerged_task_repo(tmp_path, project):
    """PROP-056/057: with a ``repo`` extra, the merge status is checked in the
    TASK's repo — un-merged deliverable there refuses done."""
    repo = _second_repo(tmp_path, "task/t-1", merged=False)
    kernel = _kernel(project)
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", cwd=project)
    _set_task_repo(kernel, "T-1", repo)

    with pytest.raises(WorktreeStateLostError):
        _tr(kernel, "T-1", "done", cwd=project)
    assert kernel.get("T-1").state == "review"
    assert WorktreeManager.branch_exists(repo, "task/t-1")  # untouched


def test_repo_aware_done_guard_finalizes_merged_task_repo(tmp_path, project):
    repo = _second_repo(tmp_path, "task/t-1", merged=True)
    kernel = _kernel(project)
    _to_execute(kernel, project)
    _tr(kernel, "T-1", "document", "review", cwd=project)
    _set_task_repo(kernel, "T-1", repo)

    _tr(kernel, "T-1", "done", cwd=project)

    assert kernel.get("T-1").state == "done"
    assert not WorktreeManager.branch_exists(repo, "task/t-1")  # cleaned up there
    logs = [e.message for e in kernel.get("T-1").work_log]
    assert any("task repo" in m for m in logs)
