"""Ownership wired into the FSM transition (HATS-955).

Drives a real ``TaskManager`` (worktree-free) with a harness-like env
(``AI_HATS_SESSION_ID`` + ``AI_HATS_ROOT_PID`` = this live process), asserting
claim-on-enter-execute, single-slot on *every* transition, release on
leaving-execute / terminal, dead-owner reclaim via the ``execute → execute``
self-loop, and inertness without a session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_tracker import TaskManager, TaskState, TrackerPaths
from ai_hats_tracker import ownership
from ai_hats_tracker.ownership import OwnershipRefused


def _mgr(tmp_path: Path) -> tuple[TaskManager, Path]:
    agent = tmp_path / ".agent"
    layout = TrackerPaths(
        tasks_dir=agent / "tasks",
        state_md_path=agent / "STATE.md",
        legacy_backlog_md=agent / "BACKLOG.md",
        ensure_base=None,
    )
    mgr = TaskManager(tmp_path, layout=layout, strict_plan_check=False, worktree_effects=None)
    return mgr, agent / "ownership.json"


@pytest.fixture
def as_agent_a(monkeypatch):
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))  # this process = live owner


def _to_execute(mgr: TaskManager, title: str) -> str:
    tid = mgr.next_id()
    mgr.create_task(tid, title)
    mgr.transition(tid, TaskState.PLAN)
    mgr.transition(tid, TaskState.EXECUTE)
    return tid


def _dead_pid() -> int:
    """A pid that is certainly dead (spawned and reaped)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_execute_claims_ownership(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    tid = _to_execute(mgr, "T1")
    rec = ownership.owner_of(reg, tid)
    assert rec is not None and rec["session_id"] == "sess-a" and rec["is_live"] is True


def test_dangling_task_blocks_any_other_transition(tmp_path: Path, as_agent_a) -> None:
    """Single-slot on every transition: while executing T1, a session cannot
    advance any *other* task — not even brainstorm→plan (HATS-955)."""
    mgr, _ = _mgr(tmp_path)
    _to_execute(mgr, "T1")  # A now owns T1
    t2 = mgr.next_id()
    mgr.create_task(t2, "T2")  # creating is fine
    with pytest.raises(OwnershipRefused):
        mgr.transition(t2, TaskState.PLAN)  # blocked: A still holds T1
    assert mgr.get_task(t2).state == TaskState.BRAINSTORM


def test_leaving_execute_releases(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    tid = _to_execute(mgr, "T1")
    assert ownership.owner_of(reg, tid) is not None
    mgr.transition(tid, TaskState.DOCUMENT)
    assert ownership.owner_of(reg, tid) is None  # freed on leaving execute


def test_walk_to_done_ends_unowned(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    tid = _to_execute(mgr, "T1")
    for state in (TaskState.DOCUMENT, TaskState.REVIEW, TaskState.DONE):
        mgr.transition(tid, state)
    assert ownership.owner_of(reg, tid) is None


def test_second_task_allowed_after_first_leaves_execute(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    t1 = _to_execute(mgr, "T1")
    mgr.transition(t1, TaskState.DOCUMENT)  # frees T1
    t2 = _to_execute(mgr, "T2")  # now allowed
    assert ownership.owner_of(reg, t2)["session_id"] == "sess-a"


def test_ownership_inert_without_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)
    mgr, reg = _mgr(tmp_path)
    # Two tasks both reach execute — no identity ⇒ no ownership, no single-slot.
    _to_execute(mgr, "T1")
    _to_execute(mgr, "T2")
    assert not reg.exists()  # no registry written at all


def test_execute_self_loop_idempotent_for_owner(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    t1 = _to_execute(mgr, "T1")
    card, _ = mgr.transition(t1, TaskState.EXECUTE)  # A re-enters its own task
    assert card.state == TaskState.EXECUTE
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"


def test_reclaim_dead_owner_via_execute_self_loop(tmp_path: Path, monkeypatch) -> None:
    mgr, reg = _mgr(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(mgr, "T1")
    ownership.take(reg, t1, "sess-a", _dead_pid())  # A crashed → its record is dead

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")  # B, live
    card, _ = mgr.transition(t1, TaskState.EXECUTE)  # reclaim self-loop (non-force)
    assert card.state == TaskState.EXECUTE
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-b"


def test_live_owner_blocks_reclaim(tmp_path: Path, monkeypatch) -> None:
    mgr, reg = _mgr(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(mgr, "T1")  # A owns, live (this process)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")  # B, also live
    with pytest.raises(OwnershipRefused):
        mgr.transition(t1, TaskState.EXECUTE)  # cannot steal a live owner
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"  # unchanged
    assert mgr.get_task(t1).state == TaskState.EXECUTE


def test_force_cannot_steal_a_live_owner(tmp_path: Path, monkeypatch) -> None:
    """Reclaim is the plain (FSM-valid) non-force execute self-loop; there is no
    force-steal — a forced same-state execute is rejected outright and the live
    owner keeps the task."""
    mgr, reg = _mgr(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(mgr, "T1")

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")
    with pytest.raises(ValueError):
        mgr.transition(t1, TaskState.EXECUTE, force=True, reason="try to steal")
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"


def test_filing_child_releases_parent_ownership(tmp_path: Path, as_agent_a) -> None:
    """HATS-977 (b): a childless task claims ownership on execute; filing a child
    epicifies the parent, which releases that hold immediately — the session is
    then free to execute the child without OwnershipRefused."""
    mgr, reg = _mgr(tmp_path)
    parent = _to_execute(mgr, "Parent")  # claims ownership while childless
    assert ownership.held_by(reg, "sess-a") == [parent]

    child = mgr.next_id()
    mgr.create_task(child, "Child", parent_task=parent)  # parent is now an epic
    assert ownership.held_by(reg, "sess-a") == []  # released at epicification

    # Session is free to work the child (single-slot no longer blocked).
    mgr.transition(child, TaskState.PLAN)
    mgr.transition(child, TaskState.EXECUTE)
    assert ownership.held_by(reg, "sess-a") == [child]


def test_epic_releases_stray_ownership_on_exit(tmp_path: Path, as_agent_a) -> None:
    """HATS-977 (a) safety-net: even if an epic still holds a stray ownership
    record (an epicification route that bypassed the create-time release),
    leaving execute drops it — the release branch no longer skips epics."""
    mgr, reg = _mgr(tmp_path)
    parent = _to_execute(mgr, "Parent")
    child = mgr.next_id()
    mgr.create_task(child, "Child", parent_task=parent)  # (b) releases here
    ownership.take(reg, parent, "sess-a", os.getpid())  # simulate a stray hold
    assert ownership.held_by(reg, "sess-a") == [parent]

    mgr.transition(parent, TaskState.DOCUMENT)  # epic leaves execute
    assert ownership.held_by(reg, "sess-a") == []  # safety-net dropped it


def test_child_transition_not_refused_after_epic_done(tmp_path: Path, as_agent_a) -> None:
    """HATS-977 repro: execute a parent while childless, file children, walk the
    (now-epic) parent to done, then transition a child — must not be
    OwnershipRefused by an orphaned parent hold."""
    mgr, reg = _mgr(tmp_path)
    parent = _to_execute(mgr, "Parent")
    c1 = mgr.next_id()
    mgr.create_task(c1, "C1", parent_task=parent)
    for state in (TaskState.DOCUMENT, TaskState.REVIEW, TaskState.DONE):
        mgr.transition(parent, state)
    assert ownership.held_by(reg, "sess-a") == []

    mgr.transition(c1, TaskState.PLAN)  # must not raise OwnershipRefused
    assert mgr.get_task(c1).state == TaskState.PLAN


def test_reparent_via_update_releases_new_parent_ownership(tmp_path: Path, as_agent_a) -> None:
    """HATS-977: re-parenting an *existing* task under X via `update`
    (`task update Y --parent-task X`) epicifies X — the same event-time release
    as the create route must fire, not only the leaving-execute safety-net."""
    mgr, reg = _mgr(tmp_path)
    x = _to_execute(mgr, "X")  # X claims ownership while childless
    assert ownership.held_by(reg, "sess-a") == [x]

    y = mgr.next_id()
    mgr.create_task(y, "Y")  # standalone backlog task, no parent
    mgr.update_task(y, parent_task=x)  # Y -> X: X is now an epic
    assert ownership.held_by(reg, "sess-a") == []  # released at the re-parent event
    assert mgr.get_task(y).parent_task == x  # Y really is X's child now


def test_unparent_via_update_does_not_resurrect_ownership(tmp_path: Path, as_agent_a) -> None:
    """HATS-977: clearing a task's parent (`task update Y --clear-parent`) leaves
    ownership untouched — the now-childless former epic is not re-claimed, and
    nothing is orphaned; it can still be finished cleanly."""
    mgr, reg = _mgr(tmp_path)
    x = _to_execute(mgr, "X")
    y = mgr.next_id()
    mgr.create_task(y, "Y", parent_task=x)  # X epic; X's hold released here
    assert ownership.held_by(reg, "sess-a") == []

    mgr.update_task(y, parent_task="")  # un-parent Y → X childless again
    assert ownership.held_by(reg, "sess-a") == []  # not resurrected, not orphaned

    for state in (TaskState.DOCUMENT, TaskState.REVIEW, TaskState.DONE):
        mgr.transition(x, state)  # childless leaf again — finishes cleanly
    assert ownership.held_by(reg, "sess-a") == []
