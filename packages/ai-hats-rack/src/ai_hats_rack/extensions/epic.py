"""Epic automation — child-driven epic sync as a post-lock extension
(HATS-1022; heirs of HATS-688/690/692/789/794).

Decisions come from the FULL child-set via the pure, table-testable
:func:`decide` (silent holes are the HATS-692 stranding class); the epic is
driven through the kernel in FSM-valid multi-hops, so every hop is journaled.
Grandparent cascade is excluded by the ``AUTOMATION_ACTOR`` marker; epics
never gain a worktree/gate on auto-hops (``is_epic`` + actor skips).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from ..dispatch import Delta, DispatchContext, Phase, Subscription
from ..events import EdgeEvent, EpicifyEvent
from ..fsm import Topology, load_topology

if TYPE_CHECKING:
    from ..kernel import Kernel

#: Actor identity of automation-driven hops; doubles as the recursion guard.
AUTOMATION_ACTOR = "rack:epic-automation"

#: Child states that no longer block their epic (HATS-690 Q2a).
RESOLVED_STATES: frozenset[str] = frozenset({"done", "cancelled"})

#: Child states that prove work has started under the epic (HATS-692 D2).
ACTIVE_STATES: frozenset[str] = frozenset({"execute", "document", "review"})

#: Epic source states the advance branch applies from. ``brainstorm`` /
#: ``plan`` are the fast-close fallback (HATS-692 plan, HATS-789 brainstorm).
ADVANCE_SOURCES: tuple[str, ...] = ("brainstorm", "plan", "execute", "document")

#: Epic source states activation applies from (an active child proves the
#: epic is decomposed — the old "leave brainstorm alone" guard is removed).
ACTIVATE_SOURCES: tuple[str, ...] = ("brainstorm", "plan")


def decide(
    epic_state: str, child_state: str, child_states: Sequence[str]
) -> tuple[str, str] | None:
    """The full coverage table: (epic source state × child trigger) → outcome.

    Returns ``("reopen" | "advance" | "activate", target_state)`` or ``None``
    for an explicit no-op. ``child_state`` is the just-mutated child;
    ``child_states`` is the epic's FULL current child-set.
    """
    if epic_state == "done":
        # Reopen: live child work under a completed epic (HATS-690 Q3).
        if child_state not in RESOLVED_STATES:
            return ("reopen", "execute")
        return None
    if epic_state in ADVANCE_SOURCES:
        resolved = bool(child_states) and all(s in RESOLVED_STATES for s in child_states)
        if resolved and any(s == "done" for s in child_states):
            return ("advance", "review")
        if epic_state in ACTIVATE_SOURCES and child_state in ACTIVE_STATES:
            return ("activate", "execute")
        return None
    return None  # review / blocked / failed / cancelled: explicit no-op


class EpicAutomationExtension:
    """Post-lock subscriber on every edge + epicify; drives the parent epic
    through the kernel (one task lock at a time — the HATS-690 rule)."""

    name = "epic-automation"

    def __init__(self, *, topology: Topology | None = None, priority: int = 30) -> None:
        self._topology = topology if topology is not None else load_topology()
        self._priority = priority
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        """Late-bound kernel handle (the kernel is built with its subscribers)."""
        self._kernel = kernel

    def subscriptions(self) -> Sequence[Subscription]:
        states = self._topology.states
        keys = [
            f"edge:{src}--{dst}"
            for src in states
            for dst in states
            if src != dst or src == "execute"  # forced edges fire any pair; + reclaim loop
        ]
        keys.append("epicify")
        return [Subscription(key, Phase.POST_LOCK, self._priority) for key in keys]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if ctx.actor == AUTOMATION_ACTOR:
            return None  # no grandparent cascade (parity: old propagate never recursed)
        kernel = self._kernel
        if kernel is None:
            raise RuntimeError("epic-automation is not bound to a kernel (call bind())")

        if isinstance(ctx.event, EpicifyEvent):
            epic_id = ctx.event.epic_id
            child = kernel.get(ctx.event.child_id)
        elif isinstance(ctx.event, EdgeEvent):
            epic_id = ctx.task.parent_task
            child = ctx.task
        else:
            return None
        if not epic_id or child is None:
            return None
        epic = kernel.get(epic_id)
        if epic is None:
            return None

        child_states = [
            c.state for cid in kernel.children_of(epic_id) if (c := kernel.get(cid)) is not None
        ]
        verdict = decide(epic.state, child.state, child_states)
        if verdict is None:
            return None
        kind, target = verdict
        from_state = epic.state

        if kind == "reopen":
            reason = f"reopened: live child {child.id} ({child.state})"
            self._hop(epic_id, "execute", ctx, reason)
            kernel.log_work(
                epic_id, f"Auto-reopened done → execute ({reason})", actor=AUTOMATION_ACTOR
            )
        elif kind == "advance":
            reason = "all children resolved (>=1 done)"
            # FSM-valid multi-hop cascade to review; each hop is a journaled
            # kernel transition.
            current = from_state
            for hop in ("plan", "execute", "document", "review"):
                if current != hop and self._topology.allows(current, hop):
                    self._hop(epic_id, hop, ctx, reason)
                    current = hop
            kernel.log_work(
                epic_id, f"Auto-advanced {from_state} -> review ({reason})", actor=AUTOMATION_ACTOR
            )
        else:  # activate
            reason = f"activated: child {child.id} ({child.state}) taken"
            if from_state == "brainstorm":
                self._hop(epic_id, "plan", ctx, reason)
            self._hop(epic_id, "execute", ctx, reason)
            kernel.log_work(
                epic_id,
                f"Auto-activated {from_state} -> execute ({reason})",
                actor=AUTOMATION_ACTOR,
            )
        to_state = kernel.get(epic_id).state
        return Delta(work_log=(f"epic {epic_id}: {kind} {from_state} -> {to_state} ({reason})",))

    def _hop(self, epic_id: str, target: str, ctx: DispatchContext, reason: str) -> None:
        if self._kernel is None:  # unreachable past on_event's bind check
            raise RuntimeError("epic-automation is not bound to a kernel")
        self._kernel.transition(
            epic_id, target, actor=AUTOMATION_ACTOR, caller_cwd=ctx.caller_cwd, reason=reason
        )
