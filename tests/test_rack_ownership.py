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

from ai_hats_core.layout import ProjectLayout

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_rack import Kernel, OperationAborted, load_topology
from ai_hats import ownership
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

    def setup(self, task_id, role="", caller_cwd=None, *, outer_deadline=None):
        return None  # non-git: no worktree path logged

    def teardown(self, task_id, *, merge=True, force=False, outer_deadline=None):
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
        OwnershipSingleSlot(registry),
        OwnershipClaim(registry),
        OwnershipRelease(registry),
    ]
    worktree = None
    if effects is not None:
        worktree = WorktreeExtension(ProjectLayout.at(tmp_path), effects=effects)
        subscribers.append(worktree)
    kernel = Kernel(agent / "tasks", prefix="T", topology=topology, subscribers=subscribers)
    if worktree is not None:
        worktree.bind(kernel)
    return kernel, registry


def _be_session(monkeypatch, session_id: str, project: Path) -> None:
    """Stand in for a session the way a launch writes one — envelope included.

    A bare ``AI_HATS_SESSION_ID`` stands in for a session no launch produces
    (HATS-1594): ``assemble_launch_env`` is the only writer and always writes
    both halves.
    """
    from ai_hats.session_identity import SessionIdentity

    identity = SessionIdentity(
        id=session_id,
        role="maintainer",
        provider="claude",
        project_dir=project,
        session_dir=project / ".agent" / "runs" / f"session_{session_id}",
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def as_agent_a(monkeypatch, tmp_path):
    _be_session(monkeypatch, "sess-a", tmp_path)
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))  # this process = live owner


def _create(kernel: Kernel, cwd: Path, task_id: str, title: str = "t", parent: str = "") -> str:
    kernel.create(actor="test", caller_cwd=cwd, task_id=task_id, title=title, parent_task=parent)
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


def test_a_half_identified_session_refuses_the_transition(tmp_path, monkeypatch):
    """HATS-1613: a torn identity must not read as "no session".

    Absence disarms the single-slot guard, and a disarmed guard hands two live
    agents the same slot — silently, whereas this refusal costs one restart.
    """
    monkeypatch.setenv("AI_HATS_SESSION_ID", "an-older-builds-session")
    kernel, reg = _kernel(tmp_path)
    _create(kernel, tmp_path, "T-1")

    with pytest.raises(OperationAborted) as exc_info:
        _tr(kernel, "T-1", "plan", cwd=tmp_path)

    assert exc_info.value.subscriber == "ownership-single-slot"
    assert "Restart the session" in exc_info.value.reason
    assert not reg.exists()
    assert kernel.get("T-1").state == "brainstorm"  # refused before any side effect


def test_execute_self_loop_idempotent_for_owner(tmp_path, as_agent_a):
    kernel, reg = _kernel(tmp_path)
    t1 = _to_execute(kernel, tmp_path, "T-1")
    result = _tr(kernel, t1, "execute", cwd=tmp_path)  # A re-enters its own task
    assert result.task.state == "execute"
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-a"


def test_reclaim_dead_owner_via_execute_self_loop(tmp_path, monkeypatch):
    kernel, reg = _kernel(tmp_path)
    _be_session(monkeypatch, "sess-a", tmp_path)
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")
    ownership.take(reg, t1, "sess-a", _dead_pid())  # A crashed → record is dead

    _be_session(monkeypatch, "sess-b", tmp_path)  # B, live
    result = _tr(kernel, t1, "execute", cwd=tmp_path)  # reclaim self-loop (non-force)
    assert result.task.state == "execute"
    assert ownership.owner_of(reg, t1)["session_id"] == "sess-b"


#: Every road OUT of `execute` in the shipped topology — the reclaim self-loop
#: included, and `done` only under `--force`. What `execute->` denotes, spelled
#: here so the test asserts the reach rather than repeating the selector.
#: `cancelled` carries the extra the card schema requires on entry; the road is
#: kept because it is one of the ways out that abandons the card.
_ROADS_OUT_OF_EXECUTE = (
    ("document", {}),
    ("blocked", {}),
    ("failed", {}),
    ("cancelled", {"resolution": "obsolete"}),
)


class _Sink:
    def __init__(self) -> None:
        self.records = []

    def record(self, record) -> None:
        self.records.append(record)


@pytest.mark.parametrize(
    "road, extra", _ROADS_OUT_OF_EXECUTE, ids=[r for r, _ in _ROADS_OUT_OF_EXECUTE]
)
def test_the_hold_is_released_on_every_road_out_of_execute(tmp_path, as_agent_a, road, extra):
    """The value of the wide selector, asserted where it is CONSUMED (HATS-1720).

    ``ownership-release`` says ``execute->`` now instead of a hand-rolled product
    of the topology's states. Measured in HATS-1719: exercising ``Selector.matches``
    in isolation proves nothing about the layer that calls it — a mutation
    disabling wide matching left 4865 tests green. So this drives the real kernel
    down each road and asks the registry, not the selector.
    """
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")
    assert ownership.owner_of(reg, tid) is not None, "precondition: execute took the hold"

    _tr(kernel, tid, road, cwd=tmp_path, **extra)

    assert ownership.owner_of(reg, tid) is None, f"leaving execute for {road!r} kept the hold"


def test_a_forced_road_out_of_execute_releases_too(tmp_path, as_agent_a):
    """`execute->done` is not a declared edge; `--force` fires the pair anyway,
    and a selector matches pairs, not the topology's edge list."""
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")
    assert ownership.owner_of(reg, tid) is not None, "precondition: execute took the hold"

    _tr(kernel, tid, "done", cwd=tmp_path, force=True, reason="shipped on master")

    assert ownership.owner_of(reg, tid) is None


def test_reclaim_keeps_the_hold_the_claim_just_took(tmp_path, as_agent_a):
    """The one place the wider selector is NOT a drop-in (design.md §6.4).

    ``execute->`` includes ``execute->execute``, which the hand-rolled product
    subtracted by hand; a selector has no subtraction operator and will not get
    one. So the filter moved into the handler — bind wide, filter inside — and
    this is its fail-under-revert test: without it the claim at priority 20 takes
    the hold on a reclaim and the release at 40 drops it in the same lock, leaving
    the card owned by nobody while an agent is standing in it.
    """
    kernel, reg = _kernel(tmp_path)
    tid = _to_execute(kernel, tmp_path, "T-1")

    _tr(kernel, tid, "execute", cwd=tmp_path)  # the reclaim self-loop

    held = ownership.owner_of(reg, tid)
    assert held is not None, "the reclaim released the hold it had just taken"
    assert held["session_id"] == "sess-a"


@pytest.mark.parametrize(
    "terminal, extra",
    [("done", {}), ("failed", {}), ("cancelled", {"resolution": "obsolete"})],
)
def test_reaching_a_terminal_drops_the_hold_whatever_road_brought_it(
    tmp_path, as_agent_a, terminal, extra
):
    """The OTHER half of the subscriber: `->done`, `->failed`, `->cancelled`.

    Held by nothing until now — narrowing the three terminals to `("done",)` left
    106 tests green, and `->done` itself was held only by a ladder-ORDER membership
    list in another file. The hold is planted directly because the road under test
    does not pass through `execute`: a card can reach a terminal from `review`,
    and the point of this half is that the hold goes whatever brought it there.
    """
    kernel, reg = _kernel(tmp_path)
    tid = _create(kernel, tmp_path, "T-1")
    _tr(kernel, tid, "plan", "execute", "document", "review", cwd=tmp_path)
    ownership.take(reg, tid, "sess-a", os.getpid())
    assert ownership.owner_of(reg, tid) is not None, "precondition: the hold is planted"

    _tr(kernel, tid, terminal, cwd=tmp_path, **extra)

    assert ownership.owner_of(reg, tid) is None, f"reaching {terminal!r} kept the hold"


def test_a_card_left_in_a_state_the_topology_no_longer_has_still_reaches_them(tmp_path, as_agent_a):
    """A wide selector reaches MORE than the product it replaced, and this is the
    difference — measured, and deliberate (HATS-1720 review).

    `ANY->ANY` matches any pair; the enumeration it replaced was drawn from
    `topology.states`. Undeclared self-loops are the difference nobody can reach
    (the kernel refuses `from == to` even under `--force`), but this one IS
    reachable: the kernel validates only the TARGET of a transition, and a card's
    state is a plain string never re-checked on load. Rename or drop a state in
    `backlog.yaml` — which ADR-0017 says is how you change the contract — and the
    cards sitting in it keep the old name.

    Before this slice such a force-close matched NO subscription: it wrote the
    state and ran nothing — no hold released, no worktree torn down, no gate, no
    consent. That is the same silence the epic exists to remove, so the wider reach
    is the point rather than a side effect.
    """
    from ai_hats_rack import Kernel
    from ai_hats_rack.fsm import Topology

    agent = tmp_path / ".agent"
    registry = agent / "ownership.json"

    def _kernel_over(states, edges):
        return Kernel(
            agent / "tasks",
            prefix="T",
            topology=Topology(initial="open", states=states, edges=edges),
            subscribers=[
                OwnershipSingleSlot(registry),
                OwnershipClaim(registry),
                OwnershipRelease(registry),
            ],
        )

    before = _kernel_over(
        ("open", "legacy", "done"), {"open": ("legacy",), "legacy": ("done",), "done": ()}
    )
    tid = _create(before, tmp_path, "T-1")
    _tr(before, tid, "legacy", cwd=tmp_path)
    ownership.take(registry, tid, "sess-a", os.getpid())

    after = _kernel_over(("open", "done"), {"open": ("done",), "done": ()})  # `legacy` renamed away
    _tr(after, tid, "done", cwd=tmp_path, force=True, reason="the state was dropped")

    assert ownership.owner_of(registry, tid) is None, (
        "a card stranded in a dropped state force-closed without releasing its hold"
    )


def test_a_declared_terminal_self_loop_still_releases(tmp_path, as_agent_a):
    """The filter subtracts the reclaim pair and NOT every self-loop (review).

    A backlog may declare `done -> done` — HATS-1719 made a self-loop under any
    name work. That edge arrives through `->done`, where release always fired.
    Skipping it strands the session: the worktree teardown next door has no
    self-loop guard, so the tree is destroyed while the hold survives, and
    single-slot then refuses every later transition that session attempts on any
    other card.
    """
    from ai_hats_rack import Kernel
    from ai_hats_rack.fsm import Topology

    topology = Topology(
        initial="open",
        states=("open", "execute", "done"),
        edges={"open": ("execute",), "execute": ("done",), "done": ("done",)},
    )
    agent = tmp_path / ".agent"
    registry = agent / "ownership.json"
    kernel = Kernel(
        agent / "tasks",
        prefix="T",
        topology=topology,
        subscribers=[
            OwnershipSingleSlot(registry),
            OwnershipClaim(registry),
            OwnershipRelease(registry),
        ],
    )
    tid = _create(kernel, tmp_path, "T-1")
    _tr(kernel, tid, "execute", "done", cwd=tmp_path)
    ownership.take(registry, tid, "sess-a", os.getpid())

    _tr(kernel, tid, "done", cwd=tmp_path)  # the declared self-loop

    assert ownership.owner_of(registry, tid) is None, "a terminal self-loop kept the hold"


def test_a_road_matching_two_of_its_selectors_runs_the_subscriber_once(tmp_path, as_agent_a):
    """``execute->failed`` matches BOTH ``execute->`` and ``->failed``.

    Measured before the dedup: the dispatcher returned the subscriber twice, so it
    applied its effect and journaled its outcome twice for one event. ``on_event``
    is handed no subscription handle and cannot tell the calls apart, so
    overlapping selectors mean the union and never the repetition (HATS-1720).
    """
    agent = tmp_path / ".agent"
    registry = agent / "ownership.json"
    sink = _Sink()
    kernel = Kernel(
        agent / "tasks",
        prefix="T",
        topology=load_topology(),
        subscribers=[
            OwnershipSingleSlot(registry),
            OwnershipClaim(registry),
            OwnershipRelease(registry),
        ],
        journal_sink=sink,
    )
    tid = _to_execute(kernel, tmp_path, "T-1")

    _tr(kernel, tid, "failed", cwd=tmp_path)

    fired = [o for o in sink.records[-1].outcomes if o.subscriber == "ownership-release"]
    assert sink.records[-1].event_key == "execute->failed"
    assert len(fired) == 1, f"the subscriber ran {len(fired)} times on one event"


def test_live_owner_blocks_reclaim(tmp_path, monkeypatch):
    kernel, reg = _kernel(tmp_path)
    _be_session(monkeypatch, "sess-a", tmp_path)
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")  # A owns, live (this process)

    _be_session(monkeypatch, "sess-b", tmp_path)  # B, also live
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
    _be_session(monkeypatch, "sess-a", tmp_path)
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    t1 = _to_execute(kernel, tmp_path, "T-1")

    _be_session(monkeypatch, "sess-b", tmp_path)
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
