"""Ownership wired into the FSM transition (HATS-955).

Drives a real ``TaskManager`` (worktree-free) with a harness-like env
(``AI_HATS_SESSION_ID`` + ``AI_HATS_ROOT_PID`` = this live process), asserting
claim-on-enter-execute, single-slot, release-on-leave-execute, ``force``, and
inertness without a session. The reclaim path (a task already in execute) is
covered separately once its verb lands.
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


def test_execute_claims_ownership(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    tid = _to_execute(mgr, "T1")
    rec = ownership.owner_of(reg, tid)
    assert rec is not None and rec["session_id"] == "sess-a"


def test_single_slot_blocks_second_execute(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    _to_execute(mgr, "T1")
    t2 = mgr.next_id()
    mgr.create_task(t2, "T2")
    mgr.transition(t2, TaskState.PLAN)
    with pytest.raises(OwnershipRefused):
        mgr.transition(t2, TaskState.EXECUTE)
    assert mgr.get_task(t2).state == TaskState.PLAN  # aborted, not saved


def test_leaving_execute_releases(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    tid = _to_execute(mgr, "T1")
    assert ownership.owner_of(reg, tid) is not None
    mgr.transition(tid, TaskState.DOCUMENT)
    assert ownership.owner_of(reg, tid) is None  # freed on leaving execute


def test_second_task_allowed_after_first_leaves_execute(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    t1 = _to_execute(mgr, "T1")
    mgr.transition(t1, TaskState.DOCUMENT)  # frees T1
    t2 = _to_execute(mgr, "T2")  # now allowed
    assert ownership.owner_of(reg, t2)["session_id"] == "sess-a"


def test_force_bypasses_single_slot(tmp_path: Path, as_agent_a) -> None:
    mgr, reg = _mgr(tmp_path)
    _to_execute(mgr, "T1")
    t2 = mgr.next_id()
    mgr.create_task(t2, "T2")
    mgr.transition(t2, TaskState.PLAN)
    mgr.transition(t2, TaskState.EXECUTE, force=True, reason="override single-slot")
    assert ownership.owner_of(reg, t2)["session_id"] == "sess-a"


def test_ownership_inert_without_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)
    mgr, reg = _mgr(tmp_path)
    # Two tasks both reach execute — single-slot never fires without an identity.
    _to_execute(mgr, "T1")
    _to_execute(mgr, "T2")
    assert not reg.exists()  # no registry written at all


def _dead_pid() -> int:
    """A pid that is certainly dead (spawned and reaped)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


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
    card, _ = mgr.transition(t1, TaskState.EXECUTE)  # reclaim self-loop
    assert card.state == TaskState.EXECUTE
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-b"


def test_live_owner_blocks_reclaim(tmp_path: Path, monkeypatch) -> None:
    mgr, reg = _mgr(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(mgr, "T1")  # A owns, live (this process)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")  # B, also live
    with pytest.raises(OwnershipRefused):
        mgr.transition(t1, TaskState.EXECUTE)
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"  # unchanged
    assert mgr.get_task(t1).state == TaskState.EXECUTE


def test_force_steals_from_live_owner(tmp_path: Path, monkeypatch) -> None:
    mgr, reg = _mgr(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(mgr, "T1")

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")
    card, _ = mgr.transition(t1, TaskState.EXECUTE, force=True, reason="force-steal")
    assert card.state == TaskState.EXECUTE
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-b"
