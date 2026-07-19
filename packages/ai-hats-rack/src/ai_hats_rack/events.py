"""Event kinds dispatched by the rack kernel (HATS-1020).

Name-your-consumer (PROP-030): an event kind exists only together with its
named consumer — see the registry table in this package's README; each class
below names its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class EdgeEvent:
    """An FSM edge being taken by ``Kernel.transition``.

    Consumers: K3 core extensions (plan-gate/ownership/worktree in-lock,
    epic-automation/views post-lock), K4 hook-runner — epic HATS-1014 §2.3.
    """

    from_state: str
    to_state: str
    #: optional declared edge name (HATS-1042 §3): adds the alias match key
    #: ``edge:<name>`` additively; the canonical key below is never affected.
    name: str = ""

    @property
    def key(self) -> str:
        return f"edge:{self.from_state}--{self.to_state}"

    @property
    def alias_key(self) -> str | None:
        return f"edge:{self.name}" if self.name else None

    @property
    def task_id(self) -> str | None:
        return None  # the transitioning card rides DispatchContext.task


@dataclass(frozen=True)
class EpicifyEvent:
    """A task changed category to epic: it gained a child via create/reparent.

    Fired on every child-add (handlers must be idempotent) — computing "first
    child" would re-introduce the frozen-category bug class (HATS-977/979).
    Consumers: K3 ownership + worktree reconciliation handlers.
    """

    epic_id: str
    child_id: str

    key: str = "epicify"


@dataclass(frozen=True)
class PreDestroyEvent:
    """Blocking pre-event of an irreversible extension operation.

    The publishing extension names the operation (e.g. ``worktree-merge``);
    blocking subscribers may abort it or extract state before destruction
    (PROP-047/058). Published via ``Kernel.publish``, never by the kernel.
    Consumers: K3 worktree guards / review-notes preservation.
    """

    operation: str
    task_id: str

    @property
    def key(self) -> str:
        return "pre-destroy"


Event = Union[EdgeEvent, EpicifyEvent, PreDestroyEvent]


def event_detail(event: Event) -> dict[str, str]:
    """Structured payload for the audit journal (K7) — what the bare key
    loses: edge endpoints, the epicified child, the pre-destroy operation."""
    if isinstance(event, EdgeEvent):
        return {"from": event.from_state, "to": event.to_state}
    if isinstance(event, EpicifyEvent):
        return {"epic": event.epic_id, "child": event.child_id}
    return {"operation": event.operation}
