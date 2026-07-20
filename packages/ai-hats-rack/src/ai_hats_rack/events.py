"""Event kinds dispatched by the rack kernel (HATS-1020).

Name-your-consumer (PROP-030): an event kind exists only together with its
named consumer — see the registry table in this package's README; each class
below names its own.

Link event keys are ``link:<kind>`` / ``unlink:<kind>`` (HATS-1043 §3): the
owning-side, in-lock event of adding/removing a link of that kind.
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


@dataclass(frozen=True)
class LinkEvent:
    """A link of ``kind`` being added to / removed from the OWNING card.

    Fired IN-LOCK inside the link mutation window on the owning side, before the
    single persist — a declared ``links.kinds[].handlers`` handler may abort it
    (HATS-1043 §3). Key: ``link:<kind>`` on add, ``unlink:<kind>`` on remove.
    The cross-backlog mirror (``link-target:<kind>``) is HATS-1044, not built.
    Consumers: declared kind handlers (the dep-cycle-check class).
    """

    kind: str
    target: str
    removed: bool = False  # False = link (add), True = unlink (remove)

    @property
    def key(self) -> str:
        return f"{'unlink' if self.removed else 'link'}:{self.kind}"

    @property
    def task_id(self) -> str | None:
        return None  # the owning card rides DispatchContext.task


@dataclass(frozen=True)
class ReadEvent:
    """A context-read enrichment point for a link ``kind`` present on the card
    being read (HATS-1064). Fired READ-phase by ``build_context`` once per
    present kind that declares a ``links.kinds[].read`` handler; never journaled
    (a read does not mutate). Key: ``read:<kind>``.

    Consumers: declared read handlers (e.g. parent-context).
    """

    kind: str

    @property
    def key(self) -> str:
        return f"read:{self.kind}"

    @property
    def task_id(self) -> str | None:
        return None  # the card being read rides DispatchContext.task


Event = Union[EdgeEvent, EpicifyEvent, PreDestroyEvent, LinkEvent, ReadEvent]


def event_detail(event: Event) -> dict[str, str]:
    """Structured payload for the audit journal (K7) — what the bare key
    loses: edge endpoints, the epicified child, the pre-destroy operation,
    the link kind + target."""
    if isinstance(event, EdgeEvent):
        return {"from": event.from_state, "to": event.to_state}
    if isinstance(event, EpicifyEvent):
        return {"epic": event.epic_id, "child": event.child_id}
    if isinstance(event, LinkEvent):
        return {"kind": event.kind, "target": event.target}
    if isinstance(event, ReadEvent):
        return {"kind": event.kind}
    return {"operation": event.operation}
