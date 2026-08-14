"""The ``wt:pre-merge`` extension-point (HATS-1540, ADR-0019 D3 / HATS-1143).

The incident that started the epic (HATS-1130) was ``ai-hats wt merge`` running
with no precondition at all, and HATS-1538 left through exactly that door. The
core fires the point; WHAT runs there is the ai-hats bundle's business, so this
suite drives a stub lifecycle and asserts the three things only the core owns:
that it fires, WHERE in the guard order, and that a veto mutates nothing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats_core.deadline import Deadline
from ai_hats_wt import (
    NOOP_LIFECYCLE,
    LifecycleContext,
    WorktreeDirtyError,
    WorktreeManager,
    WorktreeMergeAborted,
)

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "t@e")
    _git(project, "config", "user.name", "T")
    _git(project, "config", "commit.gpgsign", "false")
    (project / "README.md").write_text("# x\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


class _Recording:
    """A lifecycle bundle that records its calls and optionally vetoes."""

    def __init__(self, *, veto: bool = False) -> None:
        self.veto = veto
        self.calls: list[str] = []
        self.seen: LifecycleContext | None = None
        self.seen_created: LifecycleContext | None = None
        self.seen_teardown: LifecycleContext | None = None

    def on_created(self, ctx: LifecycleContext) -> None:
        self.calls.append("on_created")
        self.seen_created = ctx

    def before_merge(self, ctx: LifecycleContext) -> None:
        self.calls.append("before_merge")
        self.seen = ctx
        if self.veto:
            raise WorktreeMergeAborted("the gate says no")

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        self.calls.append(f"before_teardown[{event}]")
        self.seen_teardown = ctx


def _worktree(repo: Path, lifecycle, *, branch: str = "task/one") -> WorktreeManager:
    # ADR-0013 D4: a non-no-op bundle must be handed an explicit state base.
    mgr = WorktreeManager(
        repo, branch_name=branch, lifecycle=lifecycle, state_dir=repo / ".wt-state"
    )
    wt = mgr.create()
    (wt / "work.txt").write_text("done\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "work")
    return mgr


def test_merge_fires_the_point_before_it_merges(repo: Path):
    """R5 of the epic: ``wt merge`` gets a precondition of its own."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)

    mgr.merge()

    assert "before_merge" in recorder.calls
    assert recorder.calls.index("before_merge") < recorder.calls.index("before_teardown[merge]")


def test_the_point_sees_the_tree_it_is_judging(repo: Path):
    """The context carries the worktree, so a check never re-derives it."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)
    expected = mgr.worktree_path

    mgr.merge()

    assert recorder.seen is not None
    assert recorder.seen.worktree_path == expected
    assert recorder.seen.branch_name == "task/one"


def test_a_veto_leaves_the_base_the_worktree_and_the_branch_untouched(repo: Path):
    """R6: a precondition, not a teardown veto (ADR-0012 / HATS-775).

    The rejected design fired after the merge commit existed and could only
    strand a worktree. Everything a merge touches is asserted here to be exactly
    as it was: no merge commit on the base, the tree still on disk, the branch
    still there — so a refusal costs the agent nothing but a retry.
    """
    recorder = _Recording(veto=True)
    mgr = _worktree(repo, recorder)
    worktree = mgr.worktree_path
    base_before = _git(repo, "rev-parse", "main").stdout.strip()

    with pytest.raises(WorktreeMergeAborted):
        mgr.merge()

    assert _git(repo, "rev-parse", "main").stdout.strip() == base_before, "the base moved"
    assert worktree.is_dir(), "the worktree was destroyed by a refused merge"
    assert "task/one" in _git(repo, "branch", "--list", "task/one").stdout
    assert "before_teardown[merge]" not in recorder.calls


def test_a_refused_merge_can_be_retried_once_the_gate_is_satisfied(repo: Path):
    """The refusal is recoverable in place — the point of firing before mutation."""
    recorder = _Recording(veto=True)
    mgr = _worktree(repo, recorder)
    with pytest.raises(WorktreeMergeAborted):
        mgr.merge()

    recorder.veto = False
    mgr.merge()

    assert _git(repo, "log", "--oneline", "main").stdout.count("work") == 1


def test_a_dirty_tree_speaks_before_the_check_does(repo: Path):
    """R6 ordering: the cheap local guards run FIRST.

    A broken or refusing check must not mask a dirty worktree — the operator
    would then chase the gate while uncommitted work sat unsaved.
    """
    recorder = _Recording(veto=True)
    mgr = _worktree(repo, recorder)
    (mgr.worktree_path / "uncommitted.txt").write_text("scratch\n")

    with pytest.raises(WorktreeDirtyError):
        mgr.merge()

    assert recorder.calls == ["on_created"], "the check ran ahead of _check_clean"


def test_discard_has_no_pre_operation_point(repo: Path):
    """Deliberate asymmetry: discard publishes nothing to the base branch."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)

    mgr.discard(force=True)

    assert "before_merge" not in recorder.calls


def test_an_already_merged_branch_does_not_fire_the_point(repo: Path):
    """Stated, not accidental: the HATS-596 short-circuit merges nothing.

    Its content is already in the base — it entered by some other route, and
    gating a teardown that publishes nothing would refuse a supported recovery
    flow rather than protect the base.
    """
    recorder = _Recording(veto=True)
    mgr = _worktree(repo, recorder)
    _git(repo, "merge", "--no-ff", "-m", "merged by hand", "task/one")

    mgr.merge()

    assert "before_merge" not in recorder.calls


def test_the_patch_integrated_short_circuit_does_not_fire_the_point(repo: Path):
    """The second flavour of the neighbour's decision (HATS-1370), pinned by HATS-1595.

    Patch-equivalent commits are torn down without merging — merging would pull
    the pre-rebase duplicates in. Publishing nothing, this road gives a
    precondition of publishing nothing to hold, and gating it would strand the
    branch the operator asked to clean up with ``--accept-drift``. Until this
    test, ADR-0019 D3's claim that both flavours are pinned held for one.
    """
    recorder = _Recording(veto=True)
    mgr = _worktree(repo, recorder)
    # The same patch under a new sha on the base: `git cherry` reads it as
    # integrated while the branch tip stays outside the base's history, which is
    # what tells the two short-circuits apart.
    (repo / "work.txt").write_text("done\n")
    # The path, never `add .`: the state dir lives in this repo, and sweeping it
    # into the commit changes the patch — `git cherry` then reads a different
    # patch-id and the short-circuit under test is not the one that runs.
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "the same work, rebased onto main")

    mgr.merge(accept_drift=True)

    assert "before_merge" not in recorder.calls
    assert "before_teardown[merge]" in recorder.calls, "HATS-823: harvest still runs"
    assert not mgr.worktree_path


def test_a_bare_core_fires_nothing(repo: Path):
    """ADR-0013 D2: the no-op bundle keeps the engine hook-agnostic."""
    mgr = WorktreeManager(repo, branch_name="task/two", lifecycle=NOOP_LIFECYCLE)
    wt = mgr.create()
    (wt / "w.txt").write_text("x\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "w")

    mgr.merge()

    assert not mgr.worktree_path


def test_the_squash_cleanup_path_does_not_fire_the_point(repo: Path):
    """RECORDED DECISION (HATS-1540 review, supervisor ruling 2026-08-09).

    `cleanup(IsolationMode.SQUASH)` commits to the base branch WITHOUT firing
    the point — a third road into the base, and the sub-agent one (`__exit__` →
    `cleanup`, `--isolation squash` on `ai-hats agent` / `execute`). ADR-0019
    asks every path that can reach a point to carry either a test that the check
    fires or a recorded decision that it must not; this is the decision.

    Firing here would be WORSE than not firing: `cleanup` suppresses a lifecycle
    veto by design (ADR-0013 D8, so a sub-agent's own error is not masked), so a
    refusal would be swallowed and the gate would look armed while passing
    everything. Making it non-suppressible changes D8's contract. Revisit
    behaviour and ADR together — this test is what makes that deliberate.
    """
    from ai_hats_wt import IsolationMode

    recorder = _Recording(veto=True)
    mgr = WorktreeManager(
        repo,
        branch_name="agent/role/sid",
        lifecycle=recorder,
        state_dir=repo / ".wt-state",
        isolation_mode=IsolationMode.SQUASH,
    )
    wt = mgr.create()
    (wt / "work.txt").write_text("done\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "work")
    base_before = _git(repo, "rev-parse", "main").stdout.strip()

    mgr.cleanup()

    assert "before_merge" not in recorder.calls, (
        "the squash-cleanup road now fires the point — that is a behaviour "
        "change, so update ADR-0019 and this decision in the same commit"
    )
    assert _git(repo, "rev-parse", "main").stdout.strip() != base_before, (
        "precondition: this path must actually publish to the base branch, "
        "otherwise the decision it records is about nothing"
    )


def test_merge_under_an_outer_deadline_clamps_the_point_to_it(repo: Path):
    """HATS-1603: the FSM road calls ``merge`` from inside rack's 30s task lock,
    so the point's budget must come from THAT lock, not from the wt lifecycle
    lock's own 60s — which is what let a 45s check outlive its caller."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)
    outer = Deadline.under_lock(5.0, lock="rack task")

    mgr.merge(outer_deadline=outer)

    assert recorder.seen is not None
    assert recorder.seen.deadline.expires_at == pytest.approx(outer.expires_at)
    assert "rack task" in recorder.seen.deadline.origin
    assert recorder.seen.deadline.budget_for(45.0) <= 5.0


def test_merge_without_an_outer_deadline_keeps_its_own_budget(repo: Path):
    """``ai-hats wt merge`` direct: no enclosing lock, so 45s stays legal."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)

    mgr.merge()

    assert recorder.seen is not None
    assert "wt lifecycle" in recorder.seen.deadline.origin
    assert recorder.seen.deadline.budget_for(45.0) == pytest.approx(45.0)


def test_discard_under_an_outer_deadline_clamps_its_hooks(repo: Path):
    """HATS-1603: the failed/cancelled edges reach ``discard`` from inside the
    rack task lock, so its wt_out hooks are bounded by that lock too."""
    recorder = _Recording()
    mgr = _worktree(repo, recorder)
    outer = Deadline.under_lock(5.0, lock="rack task")

    mgr.discard(force=True, outer_deadline=outer)

    assert recorder.seen_teardown is not None
    assert "rack task" in recorder.seen_teardown.deadline.origin
    assert recorder.seen_teardown.deadline.budget_for(45.0) <= 5.0


def test_create_under_an_outer_deadline_clamps_its_hooks(repo: Path):
    """The ``-> execute`` edge is in-lock too, so wt_in hooks inherit the same
    ceiling — the third site of the class HATS-1603 closes."""
    recorder = _Recording()
    outer = Deadline.under_lock(5.0, lock="rack task")
    mgr = WorktreeManager(
        repo, branch_name="task/two", lifecycle=recorder, state_dir=repo / ".wt-state"
    )

    mgr.create(outer_deadline=outer)

    assert recorder.seen_created is not None
    assert "rack task" in recorder.seen_created.deadline.origin
    assert recorder.seen_created.deadline.budget_for(45.0) <= 5.0
