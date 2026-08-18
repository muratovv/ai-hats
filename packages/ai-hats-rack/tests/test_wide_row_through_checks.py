"""A WIDE row, driven through the layer that consumes it (HATS-1719 review).

The shipped done-gate binds ``->done`` — every road into the state — and until
this file nothing exercised a wide row through ``CheckSubscriber`` or
``classify_bindings`` at all. Measured by mutation: disabling wide matching in
``_fires_on`` left 4865 tests passing, i.e. the gate could silently stop firing
on every road and only one subprocess test on one edge would have noticed.

``test_selectors.py`` does not cover this: it exercises ``Selector.matches`` in
isolation, and the defect lives in the layer that calls it.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.checks import ARMED, DEAD, CheckDeclaration, CheckSubscriber, classify_bindings
from ai_hats_rack.dispatch import DispatchContext
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology
from ai_hats_rack.models import TaskCard

#: Nine states, and eight roads into `done` — the measured shape of the hole:
#: the teardown-merge fired on 8 of 8 and the gate covered 1.
_STATES = ("brainstorm", "plan", "execute", "document", "review", "done", "blocked", "failed")
_ROADS_INTO_DONE = tuple(s for s in _STATES if s != "done")


def _topology() -> Topology:
    return Topology(
        initial="brainstorm",
        states=_STATES,
        edges={s: (("done",) if s != "done" else ()) for s in _STATES},
    )


class _Port:
    def __init__(self, *rows: CheckDeclaration) -> None:
        self._rows = rows
        self.ran: list[str] = []

    def check_declarations(self):
        return self._rows

    def run_check(self, request):
        from ai_hats_rack.checks import CheckOutcome

        self.ran.append(request.event)
        return CheckOutcome(ok=True)


def _row(at: str) -> CheckDeclaration:
    return CheckDeclaration(
        path=("tasks",), at=(at,), cargo={}, on_error="refuse", label="gate", handle=object()
    )


def _ctx(source: str) -> DispatchContext:
    from pathlib import Path

    return DispatchContext(
        event=EdgeEvent(from_state=source, to_state="done"),
        task=TaskCard(id="T-1"),
        caller_cwd=Path.cwd(),
        is_epic=False,
        actor="test",
        force=False,
    )


def test_a_wide_row_runs_on_every_road_into_the_state():
    """The value of the slice, asserted where the row is consumed."""
    port = _Port(_row("->done"))
    subscriber = CheckSubscriber(port, topology=_topology(), backlog="tasks")

    for source in _ROADS_INTO_DONE:
        subscriber.on_event(_ctx(source))

    assert port.ran == [f"{s}->done" for s in _ROADS_INTO_DONE]
    assert len(port.ran) == 7, "a road into `done` did not reach the gate"


def test_the_narrow_row_it_replaced_runs_on_exactly_one():
    """The discriminator. Without it, a subscriber that ran EVERYTHING would pass
    the test above while proving nothing about the selector."""
    port = _Port(_row("review->done"))
    subscriber = CheckSubscriber(port, topology=_topology(), backlog="tasks")

    for source in _ROADS_INTO_DONE:
        subscriber.on_event(_ctx(source))

    assert port.ran == ["review->done"]


def test_a_forced_road_is_not_special_to_the_gate():
    """`execute->done` is not a legal edge of this topology, and a forced move
    fires it anyway — the road the measurement found unguarded."""
    port = _Port(_row("->done"))
    CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(_ctx("execute"))

    assert port.ran == ["execute->done"]


@pytest.mark.parametrize(
    "at, status", [("->done", ARMED), ("review->done", ARMED), ("->nowhere", DEAD)]
)
def test_the_report_judges_a_wide_selector_against_the_same_product(at, status):
    (row,) = classify_bindings((_row(at),), {"tasks": _topology()})
    assert (row.status, row.selector) == (status, at)
