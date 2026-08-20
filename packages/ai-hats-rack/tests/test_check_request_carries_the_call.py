"""What ONE firing tells the executor about itself (HATS-1724).

Two facts the request did not carry, and a script therefore could not see: WHO
moved the card, and WHICH declaration called — the second only becomes a
question once wide selectors exist, because a row bound `->done` fires on eight
roads and `event` names the road, not the row.

The actor matters because automation makes real moves: the epic extension hops
`done->execute` under `rack:epic-automation`, and a gate on `->execute` cannot
otherwise tell that from a person re-opening the epic by hand.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.checks import CheckDeclaration, CheckOutcome, CheckRequest, CheckSubscriber
from ai_hats_rack.dispatch import DispatchContext
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology
from ai_hats_rack.models import TaskCard

_STATES = ("brainstorm", "plan", "execute", "review", "done")


def _topology() -> Topology:
    return Topology(
        initial="brainstorm",
        states=_STATES,
        edges={
            "brainstorm": ("plan",),
            "plan": ("execute",),
            "execute": ("review",),
            "review": ("done",),
            "done": ("execute",),
        },
    )


class _Port:
    def __init__(self, *rows: CheckDeclaration) -> None:
        self._rows = rows
        self.seen: list[CheckRequest] = []

    def check_declarations(self):
        return self._rows

    def run_check(self, request: CheckRequest) -> CheckOutcome:
        self.seen.append(request)
        return CheckOutcome(ok=True)


def _row(*at: str) -> CheckDeclaration:
    return CheckDeclaration(
        path=("tasks",), at=at, cargo={}, on_error="refuse", label="gate", handle=object()
    )


def _ctx(source: str, target: str, actor: str) -> DispatchContext:
    return DispatchContext(
        event=EdgeEvent(from_state=source, to_state=target),
        task=TaskCard(id="T-1"),
        caller_cwd=Path.cwd(),
        is_epic=False,
        actor=actor,
        force=False,
    )


def _fire(port: _Port, source: str, target: str, actor: str) -> CheckRequest:
    CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(
        _ctx(source, target, actor)
    )
    return port.seen[-1]


def test_the_request_names_who_moved_the_card():
    """The automation actor is the one fact no other channel carries: the hop is
    an in-process nested transition, so the session environment is identical."""
    port = _Port(_row("->execute"))

    request = _fire(port, "done", "execute", "rack:epic-automation")

    assert request.actor == "rack:epic-automation"


def test_the_request_names_the_declaration_that_called_not_only_the_road():
    """A wide row fires on many roads; `event` names the road it took, and until
    now nothing said which row was answering."""
    port = _Port(_row("->done"))

    request = _fire(port, "review", "done", "human:fedor")

    assert request.selector == "->done"
    assert request.event == "review->done"


def test_the_selector_is_the_one_that_matched_when_a_row_binds_several():
    """First match in declaration order wins — the row's own spelling, so a
    script that binds both a narrow and a wide point can tell them apart."""
    port = _Port(_row("plan->execute", "->execute"))

    request = _fire(port, "plan", "execute", "human:fedor")

    assert request.selector == "plan->execute"
