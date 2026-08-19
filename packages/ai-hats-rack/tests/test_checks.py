"""The rack's half of the ``checks:`` channel (HATS-1541, ADR-0019 D11).

The arrow grammar, the filter against the topology this kernel runs, and
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
    classify_bindings,
)
from ai_hats_rack.selectors import ANY, Selector, parse_selector
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.fsm import Topology, all_edges
from ai_hats_rack.kernel import LOCK_TIMEOUT
from ai_hats_rack.models import TaskCard


def _topology() -> Topology:
    return Topology(
        initial="open",
        states=("open", "review", "done"),
        edges={"open": ("review",), "review": ("done",), "done": ()},
    )


def _ctx(
    event_key: str = "review->done", *, lock_expires_at: float | None = None
) -> DispatchContext:
    src, dst = event_key.split("->")
    return DispatchContext(
        event=EdgeEvent(from_state=src, to_state=dst),
        task=TaskCard(id="T-1"),
        caller_cwd=__import__("pathlib").Path.cwd(),
        is_epic=False,
        actor="test",
        lock_expires_at=lock_expires_at,
    )


def _row(point: str, *, on_error: str = "refuse", backlog: str = "tasks") -> CheckDeclaration:
    return CheckDeclaration(
        path=(backlog,),
        at=(point,),
        cargo={},
        on_error=on_error,
        label=f"row on {point}",
        handle=point,
    )


class _Port:
    """A carrier stub. ``run_check`` records and answers from ``outcomes``."""

    def __init__(self, *rows: CheckDeclaration, outcome: CheckOutcome | None = None) -> None:
        self._rows = rows
        self._outcome = outcome or CheckOutcome(ok=True)
        self.ran: list[str] = []
        self.budgets: list[float] = []
        self.ceilings: list[float | None] = []

    def check_declarations(self):
        return self._rows

    def run_check(self, request):
        self.ran.append(request.event or request.declaration.points()[0])
        self.budgets.append(request.timeout)
        self.ceilings.append(request.lock_expires_at)
        return self._outcome


class _PortWithoutExecutor:
    def __init__(self, *rows: CheckDeclaration) -> None:
        self._rows = rows

    def check_declarations(self):
        return self._rows


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("review->done", Selector("review", "done")),
        ("execute->execute", Selector("execute", "execute")),
        ("->done", Selector("ANY", "done")),
        # A name holding no arrow belongs to some other application's grammar,
        # and ``None`` says exactly that — it is not an error (ADR-0019 D11).
        ("wt:pre-merge", None),
        ("card:pre-create", None),
        ("review", None),
        ("review--done", None),
    ],
)
def test_only_this_packages_grammar_parses(selector, expected):
    assert parse_selector(selector) == expected


def test_the_subscription_covers_the_state_product_at_the_reserved_slot():
    """The full product, not just legal edges: a forced transition fires a real
    non-topology pair, and the gate must not be the thing force bypasses.

    ONE selector says it since HATS-1720, so the guarantee is asserted AGAINST the
    product instead of being spelled as it: what the enumeration bought was that
    every dispatchable pair reaches the gate, and that is what is checked here.
    """
    topology = _topology()
    subs = CheckSubscriber(_Port(), topology=topology, backlog="tasks").subscriptions()

    assert [s.selector for s in subs] == [Selector(ANY, ANY)]
    assert {s.phase for s in subs} == {Phase.IN_LOCK}
    assert {s.priority for s in subs} == {CHECK_PRIORITY}
    unreached = [e for e in all_edges(topology) if not subs[0].selector.matches(e)]
    assert not unreached, f"these roads stopped reaching the gate: {unreached}"


def test_a_point_of_another_topology_is_skipped_not_refused():
    """D11 clause 2. The carrier cannot tell a typo from a sibling backlog's
    point; this instance answers only for edges it actually has."""
    port = _Port(_row("plan->execute"), _row("hyp:promoted"))
    subscriber = CheckSubscriber(port, topology=_topology(), backlog="tasks")

    assert subscriber.on_event(_ctx("open->review")) is None
    assert subscriber.on_event(_ctx("review->done")) is None
    assert port.ran == []


def test_the_topology_filter_holds_even_when_the_event_key_matches():
    """The filter is not a duplicate of "did we subscribe to this key".

    Ordinarily the dispatcher only delivers keys ``subscriptions()`` asked for,
    so matching the event key would be enough — until the subscriber is wired
    against a topology the kernel is not running. That divergence is exactly
    what ADR-0019 D11 exists to make impossible, so the subscriber decides from
    its own topology rather than trusting whoever wired it.
    """
    port = _Port(_row("plan->execute"))
    subscriber = CheckSubscriber(port, topology=_topology(), backlog="tasks")

    assert subscriber.on_event(_ctx("plan->execute")) is None
    assert port.ran == []


def test_a_point_of_this_topology_runs_and_carries_the_rack_owned_budget():
    """R12/D11 clause 3: the deadline is the rack's, shipped in the request, so
    the two sides cannot keep constants that drift apart."""
    port = _Port(_row("review->done"))

    assert CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(_ctx()) is None
    assert port.ran == ["review->done"]
    assert port.budgets == [EDGE_CHECK_TIMEOUT_S]
    assert EDGE_CHECK_TIMEOUT_S < LOCK_TIMEOUT


def test_a_refusing_check_aborts_with_the_childs_own_reason():
    port = _Port(_row("review->done"), outcome=CheckOutcome(ok=False, reason="drain notes"))

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(_ctx())

    assert exc_info.value.reason == "drain notes"


def test_a_broken_check_is_downgraded_only_when_the_row_said_warn():
    broke = CheckOutcome(ok=False, reason="ruff exploded", downgradable=True)
    warned = CheckSubscriber(
        _Port(_row("review->done", on_error="warn"), outcome=broke),
        topology=_topology(),
        backlog="tasks",
    )

    delta = warned.on_event(_ctx())
    assert delta is not None
    assert "downgraded by on_error: warn" in delta.work_log[0]

    with pytest.raises(AbortOperation):
        CheckSubscriber(
            _Port(_row("review->done"), outcome=broke),
            topology=_topology(),
            backlog="tasks",
        ).on_event(_ctx())


def test_a_refusal_is_never_downgraded_by_warn():
    """``downgradable`` is the carrier saying "my substrate broke". A check that
    REFUSED is a verdict, and ``on_error: warn`` does not overrule a verdict."""
    verdict = CheckOutcome(ok=False, reason="drain notes", downgradable=False)

    with pytest.raises(AbortOperation):
        CheckSubscriber(
            _Port(_row("review->done", on_error="warn"), outcome=verdict),
            topology=_topology(),
            backlog="tasks",
        ).on_event(_ctx())


def test_no_executor_is_decided_per_row_not_per_process():
    """D11 clause 4. Refusing everything bricks a rack that has no integrator;
    passing everything is the silence the channel exists to remove."""
    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(
            _PortWithoutExecutor(_row("review->done")),
            topology=_topology(),
            backlog="tasks",
        ).on_event(_ctx())
    assert "no check executor" in exc_info.value.reason

    delta = CheckSubscriber(
        _PortWithoutExecutor(_row("review->done", on_error="warn")),
        topology=_topology(),
        backlog="tasks",
    ).on_event(_ctx())
    assert delta is not None
    assert "no check executor" in delta.work_log[0]


def test_a_port_older_than_the_protocol_does_not_raise_attributeerror_in_the_lock():
    """``getattr`` probing, not ``port.check_declarations`` — a Protocol that
    grew must not turn an integrator skew into a traceback under the lock."""

    class _Ancient:
        pass

    CheckSubscriber(_Ancient(), topology=_topology(), backlog="tasks").on_event(_ctx())


def test_a_port_that_cannot_be_asked_for_declarations_says_so_instead_of_passing():
    """The skew above must not ALSO be silent: answering "nothing declared" is
    every declared gate vanishing with nothing written anywhere (review F1)."""

    class _Ancient:
        pass

    delta = CheckSubscriber(_Ancient(), topology=_topology(), backlog="tasks").on_event(_ctx())

    assert delta is not None, "a port with no check_declarations passed unremarked"
    assert "check_declarations" in delta.work_log[0]
    assert "ungated" in delta.work_log[0]


def test_a_carrier_that_raises_becomes_a_typed_refusal():
    class _Exploding:
        def check_declarations(self):
            raise RuntimeError("the role could not be composed")

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(_Exploding(), topology=_topology(), backlog="tasks").on_event(_ctx())

    assert "the role could not be composed" in exc_info.value.reason


# --- HATS-1545 R10: addressing a backlog by name ---


def test_a_row_for_a_sibling_backlog_is_skipped_not_refused():
    """A workspace mounts several backlogs and each gets its own subscriber, so
    a row addressed to `hyp` must not brick every `tasks` transition."""
    port = _Port(_row("review->done", backlog="hyp"))

    delta = CheckSubscriber(
        port, topology=_topology(), backlog="tasks", known_backlogs=("tasks", "hyp")
    ).on_event(_ctx())

    assert delta is None
    assert port.ran == []


def test_a_row_naming_no_mounted_backlog_is_a_loud_refusal():
    """The other half of the same decision: a name nothing answers to is a typo,
    and a typo that installs no gate silently is what the channel exists to
    remove. `instance_by_name` took a first match before HATS-1545 (D8)."""
    port = _Port(_row("review->done", backlog="cards"))

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(
            port, topology=_topology(), backlog="tasks", known_backlogs=("tasks", "hyp")
        ).on_event(_ctx())

    assert "cards" in exc_info.value.reason
    assert "tasks" in exc_info.value.reason
    assert port.ran == []


def test_an_unaddressable_row_only_refuses_the_edge_it_names():
    """HATS-1576: a project whose backlog is `blog` or `dotfiles` — named that
    from birth, not renamed — mounts no `tasks`, and every shipped row addresses
    `apps.rack.tasks`. Refusing on the row's OWN edge is the gate doing its job;
    refusing on `open--review` too is the tracker bricked, and `--force` cannot
    reach it (ctx.force is passed inside CheckRequest, i.e. after this point).
    """  # comment-length: allow — which edge dies is the whole defect
    port = _Port(_row("review->done", backlog="tasks"))
    subscriber = CheckSubscriber(
        port, topology=_topology(), backlog="blog", known_backlogs=("blog",)
    )

    assert subscriber.on_event(_ctx("open->review")) is None
    assert port.ran == []

    with pytest.raises(AbortOperation):
        subscriber.on_event(_ctx("review->done"))


def test_the_unmounted_name_refusal_names_the_way_out():
    """A refusal that only states the mismatch sends the reader to the shipped
    role; the fix is one line in their own backlog.yaml (HATS-1576)."""
    port = _Port(_row("review->done", backlog="tasks"))

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(
            port, topology=_topology(), backlog="blog", known_backlogs=("blog",)
        ).on_event(_ctx())

    reason = exc_info.value.reason
    assert "cli_alias" in reason, "the refusal must name the alias that re-addresses it"
    assert "apps.rack.blog" in reason, "…and the alternative: re-address the row"


def test_a_rack_row_that_names_no_backlog_at_all_is_a_loud_refusal():
    """`apps.rack` with rows directly under it skips the level that says WHICH
    backlog — the qualification the DSL makes unwritable-by-omission."""
    port = _Port(
        CheckDeclaration(
            path=(),
            at=("review->done",),
            cargo={},
            on_error="refuse",
            label="row with no backlog",
            handle="x",
        )
    )

    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(_ctx())

    assert "apps.rack.<backlog>" in exc_info.value.reason


def test_a_row_binding_several_points_fires_once_per_event():
    """`at:` is a list, and the subscriber matches the event being fired — a row
    on two edges is one row, not two gates on each."""
    port = _Port(
        CheckDeclaration(
            path=("tasks",),
            at=("open->review", "review->done"),
            cargo={},
            on_error="refuse",
            label="two-point row",
            handle="x",
        )
    )
    subscriber = CheckSubscriber(port, topology=_topology(), backlog="tasks")

    assert subscriber.on_event(_ctx()) is None

    assert port.ran == ["review->done"]


def test_a_row_addressed_by_the_backlogs_cli_alias_fires(tmp_path):
    """HATS-1545 F4. ADR-0017 §3 promises `name` OR `cli_alias` addresses a
    backlog. Matching only the name sent an aliased row down the quiet
    sibling-backlog branch: too known to refuse, too unequal to fire — a gate the
    author wrote, that no message ever mentions again."""
    port = _Port(_row("review->done", backlog="cards"))

    delta = CheckSubscriber(
        port,
        topology=_topology(),
        backlog=("tasks", "cards"),
        known_backlogs=("tasks", "cards"),
    ).on_event(_ctx())

    assert delta is None
    assert port.ran == ["review->done"], "the aliased row must actually run"


def test_the_request_carries_the_lock_ceiling_the_kernel_minted():
    """HATS-1603: the executor cannot bound a check by a lock it cannot see, so
    the ceiling rides the request. A bare float — the rack has no Deadline."""
    port = _Port(_row("review->done"))

    CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(
        _ctx(lock_expires_at=1234.5)
    )

    assert port.ceilings == [1234.5]


def test_an_unlocked_firing_ships_no_ceiling():
    """No enclosing lock declared -> the executor bounds by its own budget."""
    port = _Port(_row("review->done"))

    CheckSubscriber(port, topology=_topology(), backlog="tasks").on_event(_ctx())

    assert port.ceilings == [None]


# ----- the doctor's classifier (HATS-1584) -----------------------------------


def _hyp_topology() -> Topology:
    """A sibling backlog's topology — no state name in common with _topology()."""
    return Topology(
        initial="active",
        states=("active", "confirmed"),
        edges={"active": ("confirmed",), "confirmed": ()},
    )


def _mounted() -> dict[str, Topology]:
    return {"tasks": _topology(), "hyp": _hyp_topology()}


def test_a_point_is_armed_foreign_or_dead_against_every_mounted_topology():
    """The distinction the subscriber cannot make (ADR-0019 D11 clause 2): it
    holds ONE topology, so a sibling's edge and a typo are the same miss to it.
    Given every mounted topology, they are three different facts."""
    rows = classify_bindings(
        [
            _row("review->done"),
            _row("active->confirmed"),
            _row("reviw->done"),
        ],
        _mounted(),
    )

    assert [(r.status, r.selector) for r in rows] == [
        ("armed", "review->done"),
        ("foreign", "active->confirmed"),
        ("dead", "reviw->done"),
    ]


def test_a_row_naming_no_mounted_backlog_is_unaddressed_in_the_subscribers_words():
    """One recipe, two readers: the doctor prints the sentence the subscriber
    raises, so the fix an operator is told outside the lock is the one the
    refusal inside it would have given."""
    row = _row("review->done", backlog="cards")

    (status,) = classify_bindings([row], _mounted())

    assert (status.status, status.selector) == ("unaddressed", "")
    with pytest.raises(AbortOperation) as exc_info:
        CheckSubscriber(
            _Port(row), topology=_topology(), backlog="tasks", known_backlogs=("tasks", "hyp")
        ).on_event(_ctx())
    assert status.detail == exc_info.value.reason


def test_a_row_at_the_wrong_depth_is_unaddressed_too():
    """`apps.rack` with no backlog under it names nothing to gate; the point is
    never examined, because there is no topology to examine it against."""
    row = CheckDeclaration(
        path=(),
        at=("review->done",),
        cargo={},
        on_error="refuse",
        label="deep row",
        handle=None,
    )

    (status,) = classify_bindings([row], _mounted())

    assert status.status == "unaddressed"
    assert "one level down" in status.detail


def test_the_alias_spelling_of_a_backlog_addresses_it():
    """ADR-0017 §3: a backlog answers to its name OR its cli_alias. Keyed on one
    spelling, the classifier would call an aliased row unaddressed — the loud
    half of HATS-1545 F4, one layer up."""
    topologies = {"tasks": _topology(), "cards": _topology(), "hyp": _hyp_topology()}

    (status,) = classify_bindings([_row("review->done", backlog="cards")], topologies)

    assert status.status == "armed"


def test_a_row_binding_several_points_gets_a_line_per_point():
    """The report is per (row, point): one row can be armed here and dead there,
    and a single verdict for the row would hide whichever half is unhappy."""
    row = CheckDeclaration(
        path=("tasks",),
        at=("review->done", "reviw->done"),
        cargo={},
        on_error="warn",
        label="two-point row",
        handle=None,
    )

    rows = classify_bindings([row], _mounted())

    assert [(r.status, r.selector) for r in rows] == [
        ("armed", "review->done"),
        ("dead", "reviw->done"),
    ]


def test_a_dead_points_detail_names_the_mounted_roster():
    """What makes it a typo rather than a row for some other project's backlog
    is the roster — so the sentence carries it (HATS-1584 R2)."""
    (status,) = classify_bindings([_row("card:pre-create")], _mounted())

    assert status.status == "dead"
    assert "hyp" in status.detail and "tasks" in status.detail
    assert "`review->done`" in status.detail and "`->done`" in status.detail
