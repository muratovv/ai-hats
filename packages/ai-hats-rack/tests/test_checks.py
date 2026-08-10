"""The rack's half of the ``checks:`` channel (HATS-1541, ADR-0019 D11).

The grammar of ``edge:``, the filter against the topology this kernel runs, and
the "subscribed but no executor" policy — all of it decided here, standalone,
with no integrator anywhere near it.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.checks import (
    CHECK_PRIORITY,
    EDGE_CHECK_TIMEOUT_S,
    CheckDeclaration,
    CheckOutcome,
    CheckSubscriber,
    parse_edge_point,
)
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology, all_edge_keys
from ai_hats_rack.kernel import LOCK_TIMEOUT
from ai_hats_rack.models import TaskCard


def _topology() -> Topology:
    return Topology(
        initial="open",
        states=("open", "review", "done"),
        edges={"open": ("review",), "review": ("done",), "done": ()},
    )


def _ctx(event_key: str = "edge:review--done") -> DispatchContext:
    src, dst = event_key.removeprefix("edge:").split("--")
    return DispatchContext(
        event=EdgeEvent(from_state=src, to_state=dst),
        task=TaskCard(id="T-1"),
        caller_cwd=__import__("pathlib").Path.cwd(),
        is_epic=False,
        actor="test",
    )


def _row(point: str, *, on_error: str = "refuse") -> CheckDeclaration:
    return CheckDeclaration(point=point, on_error=on_error, label=f"row on {point}", handle=point)


class _Port:
    """A carrier stub. ``run_check`` records and answers from ``outcomes``."""

    def __init__(self, *rows: CheckDeclaration, outcome: CheckOutcome | None = None) -> None:
        self._rows = rows
        self._outcome = outcome or CheckOutcome(ok=True)
        self.ran: list[str] = []
        self.budgets: list[float] = []

    def check_declarations(self):
        return self._rows

    def run_check(self, request):
        self.ran.append(request.declaration.point)
        self.budgets.append(request.timeout)
        return self._outcome


class _PortWithoutExecutor:
    def __init__(self, *rows: CheckDeclaration) -> None:
        self._rows = rows

    def check_declarations(self):
        return self._rows


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ("edge:review--done", ("review", "done")),
        ("edge:execute--execute", ("execute", "execute")),
        ("edge:review--done--extra", ("review", "done--extra")),
        ("wt:pre-merge", None),
        ("card:pre-create", None),
        ("edge:review", None),
        ("edge:--done", None),
        ("edge:review--", None),
        ("review--done", None),
    ],
)
def test_only_this_packages_namespace_parses(point, expected):
    assert parse_edge_point(point) == expected


def test_subscriptions_cover_the_state_product_at_the_reserved_slot():
    """The full product, not just legal edges: a forced transition fires a real
    non-topology key, and the gate must not be the thing force bypasses."""
    topology = _topology()
    subs = CheckSubscriber(_Port(), topology=topology).subscriptions()

    assert {s.event_key for s in subs} == set(all_edge_keys(topology))
    assert {s.phase for s in subs} == {Phase.IN_LOCK}
    assert {s.priority for s in subs} == {CHECK_PRIORITY}


def test_a_point_of_another_topology_is_skipped_not_refused():
    """D11 clause 2. The carrier cannot tell a typo from a sibling backlog's
    point; this instance answers only for edges it actually has."""
    port = _Port(_row("edge:plan--execute"), _row("hyp:promoted"))
    subscriber = CheckSubscriber(port, topology=_topology())

    assert subscriber.on_event(_ctx("edge:open--review")) is None
    assert subscriber.on_event(_ctx("edge:review--done")) is None
    assert port.ran == []


def test_a_point_of_this_topology_runs_and_carries_the_rack_owned_budget():
    """R12/D11 clause 3: the deadline is the rack's, shipped in the request, so
    the two sides cannot keep constants that drift apart."""
    port = _Port(_row("edge:review--done"))

    assert CheckSubscriber(port, topology=_topology()).on_event(_ctx()) is None
    assert port.ran == ["edge:review--done"]
    assert port.budgets == [EDGE_CHECK_TIMEOUT_S]
    assert EDGE_CHECK_TIMEOUT_S < LOCK_TIMEOUT


def test_a_refusing_check_aborts_with_the_childs_own_reason():
    port = _Port(_row("edge:review--done"), outcome=CheckOutcome(ok=False, reason="drain notes"))

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(port, topology=_topology()).on_event(_ctx())

    assert exc_info.value.reason == "drain notes"


def test_a_broken_check_is_downgraded_only_when_the_row_said_warn():
    broke = CheckOutcome(ok=False, reason="ruff exploded", downgradable=True)
    warned = CheckSubscriber(
        _Port(_row("edge:review--done", on_error="warn"), outcome=broke), topology=_topology()
    )

    delta = warned.on_event(_ctx())
    assert delta is not None
    assert "downgraded by on_error: warn" in delta.work_log[0]

    with pytest.raises(AbortOperation):
        CheckSubscriber(
            _Port(_row("edge:review--done"), outcome=broke), topology=_topology()
        ).on_event(_ctx())


def test_a_refusal_is_never_downgraded_by_warn():
    """``downgradable`` is the carrier saying "my substrate broke". A check that
    REFUSED is a verdict, and ``on_error: warn`` does not overrule a verdict."""
    verdict = CheckOutcome(ok=False, reason="drain notes", downgradable=False)

    with pytest.raises(AbortOperation):
        CheckSubscriber(
            _Port(_row("edge:review--done", on_error="warn"), outcome=verdict),
            topology=_topology(),
        ).on_event(_ctx())


def test_no_executor_is_decided_per_row_not_per_process():
    """D11 clause 4. Refusing everything bricks a rack that has no integrator;
    passing everything is the silence the channel exists to remove."""
    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(
            _PortWithoutExecutor(_row("edge:review--done")), topology=_topology()
        ).on_event(_ctx())
    assert "no check executor" in exc_info.value.reason

    delta = CheckSubscriber(
        _PortWithoutExecutor(_row("edge:review--done", on_error="warn")), topology=_topology()
    ).on_event(_ctx())
    assert delta is not None
    assert "no check executor" in delta.work_log[0]


def test_a_port_older_than_the_protocol_does_not_raise_attributeerror_in_the_lock():
    """``getattr`` probing, not ``port.check_declarations`` — a Protocol that
    grew must not turn an integrator skew into a traceback under the lock."""

    class _Ancient:
        pass

    assert CheckSubscriber(_Ancient(), topology=_topology()).on_event(_ctx()) is None


def test_a_carrier_that_raises_becomes_a_typed_refusal():
    class _Exploding:
        def check_declarations(self):
            raise RuntimeError("the role could not be composed")

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(_Exploding(), topology=_topology()).on_event(_ctx())

    assert "the role could not be composed" in exc_info.value.reason
