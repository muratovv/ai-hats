"""A subscriber runs ONCE per event and phase, however many of its selectors match.

Wide selectors are what makes this reachable (HATS-1720): before them a subscriber
enumerated distinct pairs and could not overlap with itself. `ownership-release`
binds `execute->` AND `->done`, and `execute->done` matches both — measured before
the rule, the dispatcher returned it twice, so it applied its effect and journaled
its outcome twice for one event.

The rule lives in THIS package, so it is pinned in this package. It was pinned
only in a consumer's test file first, and the package's own 1012 tests stayed green
with the dedup neutered — the HATS-1719 shape one layer up.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.dispatch import Dispatcher, Phase, Subscription
from ai_hats_rack.selectors import ANY, Edge, Selector


class _Sub:
    def __init__(self, name: str, *subs: Subscription) -> None:
        self.name = name
        self._subs = subs

    def subscriptions(self):
        return self._subs

    def on_event(self, ctx):  # pragma: no cover — never dispatched here
        return None


def _names(dispatcher: Dispatcher, edge: Edge, **kwargs) -> list[str]:
    return [s.name for s in dispatcher.subscribers_for_edge(edge, Phase.IN_LOCK, **kwargs)]


@pytest.mark.parametrize(
    "first, second",
    [
        (Selector("execute", ANY), Selector(ANY, "done")),  # the shipped shape
        (Selector(ANY, ANY), Selector("execute", "done")),  # everywhere plus exact
        (Selector(ANY, ANY), Selector(ANY, ANY)),  # the same selector twice
    ],
)
def test_two_matching_selectors_of_one_subscriber_run_it_once(first, second):
    sub = _Sub(
        "release", Subscription(first, Phase.IN_LOCK, 40), Subscription(second, Phase.IN_LOCK, 40)
    )

    assert _names(Dispatcher([sub]), Edge("execute", "done")) == ["release"]


def test_two_subscribers_are_not_collapsed_into_one():
    """The discriminator: dedup is per SUBSCRIBER, never per selector — two
    instances that happen to share a name and a selector both run."""
    a = _Sub("gate", Subscription(Selector(ANY, ANY), Phase.IN_LOCK, 10))
    b = _Sub("gate", Subscription(Selector(ANY, ANY), Phase.IN_LOCK, 20))

    assert _names(Dispatcher([a, b]), Edge("execute", "done")) == ["gate", "gate"]


def test_the_subscriber_keeps_its_earliest_slot():
    """The second half of the contract: a subscriber cannot be pushed DOWN the
    ladder by owning a second, later binding.

    `first` books priority 5 and would run before `middle`; its own wide selector
    at 50 also matches. Keeping the later slot would silently reorder the ladder —
    and an in-lock ladder is an order of side effects, so that is a behaviour
    change, not a cosmetic one.
    """
    first = _Sub(
        "single-slot",
        Subscription(Selector("execute", "done"), Phase.IN_LOCK, 5),
        Subscription(Selector(ANY, ANY), Phase.IN_LOCK, 50),
    )
    middle = _Sub("gate", Subscription(Selector(ANY, ANY), Phase.IN_LOCK, 15))

    assert _names(Dispatcher([first, middle]), Edge("execute", "done")) == ["single-slot", "gate"]


def test_a_named_edge_alias_does_not_run_the_subscriber_twice():
    """The third route into one list: `edge:<name>` rows are merged in beside the
    selectors, so a subscriber holding both must still run once."""
    sub = _Sub(
        "reclaim-watcher",
        Subscription(Selector("execute", ANY), Phase.IN_LOCK, 40),
        Subscription("edge:reclaim", Phase.IN_LOCK, 40),
    )

    assert _names(Dispatcher([sub]), Edge("execute", "execute"), alias_key="edge:reclaim") == [
        "reclaim-watcher"
    ]


def test_a_non_fsm_key_declared_twice_runs_the_subscriber_once():
    """The rule is stated without qualification in ADR-0017 §3, so it has to hold
    on the key route as well — no shipped subscriber can reach this today, which is
    exactly why it would rot unwatched."""
    sub = _Sub(
        "release",
        Subscription("epicify", Phase.POST_LOCK, 10),
        Subscription("epicify", Phase.POST_LOCK, 20),
    )

    ran = Dispatcher([sub]).subscribers_for("epicify", Phase.POST_LOCK)

    assert [s.name for s in ran] == ["release"]
