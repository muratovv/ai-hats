"""Ported group 8 (incidents §4): ownership wired into rack transitions
(HATS-955/977/979) — the whole of ``pt/test_ownership_transition.py`` adapted
to the rack dispatcher API.

Claim-on-enter-execute, single-slot on every transition, unconditional
release on leaving-execute/terminal, dead-owner reclaim via the
``execute → execute`` self-loop, inertness without a session, and the
epicification reconciliation (release + ``discard_if_empty``). Refusals are
typed ``OperationAborted`` with actionable text — never a raw traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_rack import Kernel, OperationAborted, load_topology
from ai_hats_tracker import ownership
from ai_hats.rack_wiring import (
    OwnershipClaim,
    OwnershipRelease,
    OwnershipSingleSlot,
    WorktreeExtension,
)

pytestmark = pytest.mark.integration


class _RecordingEffects:
    """Worktree-effects double recording ``discard_if_empty`` (HATS-979)."""

    def __init__(self) -> None:
        self.reclaimed: list[str] = []

    def setup(self, task_id, role="", caller_cwd=None):
        return None  # non-git: no worktree path logged

    def teardown(self, task_id, *, merge=True, force=False):
        return None

    def assert_canonical_base(self):
        pass

    def discard_if_empty(self, task_id):
        self.reclaimed.append(task_id)
        return True


def _kernel(tmp_path: Path, effects=None) -> tuple[Kernel, Path]:
    agent = tmp_path / ".agent"
    registry = agent / "ownership.json"
    topology = load_topology()
    subscribers = [
        OwnershipSingleSlot(registry, topology=topology),
        OwnershipClaim(registry, topology=topology),
        OwnershipRelease(registry, topology=topology),
    ]
    worktree = None
    if effects is not None:
        worktree = WorktreeExtension(tmp_path, effects=effects, topology=topology)
        subscribers.append(worktree)
    kernel = Kernel(agent / "tasks", prefix="T", topology=topology, subscribers=subscribers)
    if worktree is not None:
        worktree.bind(kernel)
    return kernel, registry


@pytest.fixture
def as_agent_a(monkeypatch):
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))  # this process = live owner


def _create(kernel: Kernel, cwd: Path, task_id: str, title: str = "t", parent: str = "") -> str:
    kernel.create(
        actor="test", caller_cwd=cwd, task_id=task_id, title=title, parent_task=parent
    )
    return task_id


def _tr(kernel: Kernel, task_id: str, *states: str, cwd: Path, **kwargs):
    for state in states:
        result = kernel.transition(task_id, state, actor="test", caller_cwd=cwd, **kwargs)
    return result


def _to_execute(kernel: Kernel, cwd: Path, task_id: str, title: str = "T") -> str:
    _create(kernel, cwd, task_id, title)
    _tr(kernel, task_id, "plan", "execute", cwd=cwd)
    return task_id


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603 — fixed argv
    proc.wait()
    return proc.pid


def test_execute_claims_ownership(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")
    rec = ownership.owner_of(reg, tid)
    assert rec is not None and rec["session_id"] == "sess-a" and rec["is_live"] is True


def test_dangling_task_blocks_any_other_transition(tmp_path, as_agent_a):
    """Single-slot on every transition: while executing T-1, the session may
    not advance any other task — not even brainstorm→plan (HATS-955)."""
    kernel, _ = _kernel(tmp_path)
    _to_execute(kernel, tmp_path, "T-1")
    _create(kernel, tmp_path, "T-2")  # creating is fine
    with pytest.raises(OperationAborted) as exc_info:
        _tr(kernel, "T-2", "plan", cwd=tmp_path)
    # Typed + actionable (the baseline pain was a raw traceback).
    assert "still holds" in exc_info.value.reason
    assert "T-1" in exc_info.value.reason
    assert kernel.get("T-2").state == "brainstorm"


def test_leaving_execute_releases(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")
    assert ownership.owner_of(reg, tid) is not None
    _tr(kernel, tid, "document", cwd=tmp_path)
    assert ownership.owner_of(reg, tid) is None  # freed on leaving execute


def test_walk_to_done_ends_unowned(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")
    _tr(kernel, tid, "document", "review", "done", cwd=tmp_path)
    assert ownership.owner_of(reg, tid) is None


def test_second_task_allowed_after_first_leaves_execute(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    t1 = _to_execute(kernel, tmp_path, "T-1")
    _tr(kernel, t1, "document", cwd=tmp_path)  # frees T-1
    t2 = _to_execute(kernel, tmp_path, "T-2")  # now allowed
    assert ownership.owner_of(reg, t2)["session_id"] == "sess-a"


def test_ownership_inert_without_session(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)
    kernel, reg = _kernel(tmp_path)
    _to_execute(kernel, tmp_path, "T-1")
    _to_execute(kernel, tmp_path, "T-2")  # no identity ⇒ no single-slot either
    assert not reg.exists()  # no registry written at all


def test_execute_self_loop_idempotent_for_owner(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    t1 = _to_execute(kernel, tmp_path, "T-1")
    result = _tr(kernel, t1, "execute", cwd=tmp_path)  # A re-enters its own task
    assert result.task.state == "execute"
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"


def test_reclaim_dead_owner_via_execute_self_loop(tmp_path, monkeypatch):
    kernel, reg = _kernel(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")
    ownership.take(reg, t1, "sess-a", _dead_pid())  # A crashed → record is dead

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")  # B, live
    result = _tr(kernel, t1, "execute", cwd=tmp_path)  # reclaim self-loop (non-force)
    assert result.task.state == "execute"
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-b"


def test_live_owner_blocks_reclaim(tmp_path, monkeypatch):
    kernel, reg = _kernel(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")  # A owns, live (this process)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")  # B, also live
    with pytest.raises(OperationAborted) as exc_info:
        _tr(kernel, t1, "execute", cwd=tmp_path)  # cannot steal a live owner
    assert "held by a live agent" in exc_info.value.reason
    assert "force does not bypass ownership" in exc_info.value.reason
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"  # unchanged
    assert kernel.get(t1).state == "execute"


def test_force_cannot_steal_a_live_owner(tmp_path, monkeypatch):
    """Reclaim is the plain non-force self-loop; a forced same-state execute
    is rejected outright and the live owner keeps the task."""
    kernel, reg = _kernel(tmp_path)
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")

    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-b")
    with pytest.raises(ValueError, match="already in state"):
        _tr(kernel, t1, "execute", cwd=tmp_path, force=True, reason="try to steal")
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"


def test_filing_child_releases_parent_ownership(tmp_path, as_agent_a):
    """HATS-977 (b): epicification via create releases the parent's hold —
    the session is then free to execute the child."""
    kernel, reg = _kernel(tmp_path)
    parent = _to_execute(kernel, tmp_path, "T-1", "Parent")
    assert ownership.held_by(reg, "sess-a") == [parent]

    _create(kernel, tmp_path, "T-2", "Child", parent=parent)  # parent is an epic now
    assert ownership.held_by(reg, "sess-a") == []  # released at epicification

    _tr(kernel, "T-2", "plan", "execute", cwd=tmp_path)
    assert ownership.held_by(reg, "sess-a") == ["T-2"]


def test_epic_releases_stray_ownership_on_exit(tmp_path, as_agent_a):
    """HATS-977 (a) safety-net: leaving execute drops even a stray epic hold —
    the release branch no longer skips epics."""
    kernel, reg = _kernel(tmp_path)
    parent = _to_execute(kernel, tmp_path, "T-1", "Parent")
    _create(kernel, tmp_path, "T-2", "Child", parent=parent)  # (b) releases here
    ownership.take(reg, parent, "sess-a", os.getpid())  # simulate a stray hold
    assert ownership.held_by(reg, "sess-a") == [parent]

    _tr(kernel, parent, "document", cwd=tmp_path)  # epic leaves execute
    assert ownership.held_by(reg, "sess-a") == []


def test_child_transition_not_refused_after_epic_done(tmp_path, as_agent_a):
    """HATS-977 repro: walk the (now-epic) parent to done, then transition a
    child — must not be refused by an orphaned parent hold."""
    kernel, reg = _kernel(tmp_path)
    parent = _to_execute(kernel, tmp_path, "T-1", "Parent")
    _create(kernel, tmp_path, "T-2", "C1", parent=parent)
    _tr(kernel, parent, "document", "review", "done", cwd=tmp_path)
    assert ownership.held_by(reg, "sess-a") == []

    _tr(kernel, "T-2", "plan", cwd=tmp_path)  # must not raise
    assert kernel.get("T-2").state == "plan"


def test_reparent_releases_new_parent_ownership(tmp_path, as_agent_a):
    """HATS-977: re-parenting an existing task under X epicifies X — the same
    event-time release as the create route fires."""
    kernel, reg = _kernel(tmp_path)
    x = _to_execute(kernel, tmp_path, "T-1", "X")
    assert ownership.held_by(reg, "sess-a") == [x]

    _create(kernel, tmp_path, "T-2", "Y")  # standalone, no parent
    kernel.set_parent("T-2", x, actor="test", caller_cwd=tmp_path)
    assert ownership.held_by(reg, "sess-a") == []  # released at the re-parent event
    assert kernel.get("T-2").parent_task == x


def test_unparent_does_not_resurrect_ownership(tmp_path, as_agent_a):
    """HATS-977: clearing a task's parent leaves ownership untouched; the
    now-childless former epic still finishes cleanly."""
    kernel, reg = _kernel(tmp_path)
    x = _to_execute(kernel, tmp_path, "T-1", "X")
    _create(kernel, tmp_path, "T-2", "Y", parent=x)  # X epic; hold released
    assert ownership.held_by(reg, "sess-a") == []

    kernel.set_parent("T-2", "", actor="test", caller_cwd=tmp_path)  # un-parent
    assert ownership.held_by(reg, "sess-a") == []  # not resurrected, not orphaned

    _tr(kernel, x, "document", "review", "done", cwd=tmp_path)  # childless leaf again
    assert ownership.held_by(reg, "sess-a") == []


def test_epicify_via_create_reclaims_parent_worktree(tmp_path, as_agent_a):
    """HATS-979: filing a child invokes ``discard_if_empty`` on the parent."""
    effects = _RecordingEffects()
    kernel, _ = _kernel(tmp_path, effects=effects)
    parent = _to_execute(kernel, tmp_path, "T-1", "Parent")
    assert effects.reclaimed == []

    _create(kernel, tmp_path, "T-2", "Child", parent=parent)
    assert effects.reclaimed == [parent]


def test_epicify_via_reparent_reclaims_parent_worktree(tmp_path, as_agent_a):
    """HATS-979: the re-parent route fires the same epicification hook."""
    effects = _RecordingEffects()
    kernel, _ = _kernel(tmp_path, effects=effects)
    x = _to_execute(kernel, tmp_path, "T-1", "X")
    _create(kernel, tmp_path, "T-2", "Y")  # standalone → no reclaim yet
    assert effects.reclaimed == []

    kernel.set_parent("T-2", x, actor="test", caller_cwd=tmp_path)
    assert effects.reclaimed == [x]
