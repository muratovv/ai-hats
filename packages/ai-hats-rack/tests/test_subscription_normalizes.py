"""A subscription spelled as an arrow STRING is an FSM subscription (HATS-1719).

The typed index routes an edge event by its pair and everything else by its key.
That split has one sharp edge: a caller who passes ``"review->done"`` as a plain
string would land in the key bucket, match no edge ever, and be disarmed in
silence — the exact failure mode this epic exists to remove. So the string is
normalized at construction rather than trusted.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.dispatch import Phase, Subscription
from ai_hats_rack.selectors import Selector


@pytest.mark.parametrize(
    "text, expected",
    [
        ("review->done", Selector("review", "done")),
        ("->done", Selector("ANY", "done")),
    ],
)
def test_an_arrow_string_becomes_a_selector(text, expected):
    assert Subscription(text, Phase.IN_LOCK).selector == expected


@pytest.mark.parametrize("key", ["epicify", "link:depends_on", "read:parent_task", "op:log"])
def test_a_non_fsm_key_stays_a_string(key):
    """Those events obey no arrow grammar and must keep the key path."""
    assert Subscription(key, Phase.POST_LOCK).selector == key


def test_asking_for_an_arrow_by_key_is_refused_not_answered_empty():
    """The other half of the same trap (HATS-1719).

    ``subscribers_for`` answers for NON-FSM keys. Handed an arrow it would find
    an empty bucket and return ``[]`` — indistinguishable from "nothing is
    subscribed", which is how a caller convinces itself a gate is absent when it
    is merely being asked the wrong way. An edge is addressed by its pair.
    """
    from ai_hats_rack.dispatch import Dispatcher

    with pytest.raises(ValueError, match="subscribers_for_edge"):
        Dispatcher().subscribers_for("review->done", Phase.IN_LOCK)


@pytest.mark.parametrize("text", ["a->b->c", "execute->", "ANY->ANY", "review -> done"])
def test_a_string_that_is_not_a_legal_selector_is_refused_not_normalized(text):
    """Normalizing without judging just relocates the trap (HATS-1719 review).

    ``parse_selector`` is deliberately wider than the legal grammar, so a
    typo'd arrow string became either a silent WILDCARD (``execute->`` fires on
    every way out) or a silently DEAD selector (``a->b->c`` matches nothing) —
    neither raising. A string goes through the public grammar; internal code
    that means a wide selector says so with a ``Selector`` object.
    """
    with pytest.raises(ValueError, match="selector"):
        Subscription(text, Phase.IN_LOCK)


def test_an_object_is_trusted_where_a_string_is_judged():
    """The escape hatch the rule needs: HATS-1720 subscribes wide from code."""
    wide = Selector("execute", "ANY")
    assert Subscription(wide, Phase.IN_LOCK).selector is wide


@pytest.mark.parametrize("key", ["link:a->b", "read:x->y"])
def test_a_namespaced_key_is_not_mistaken_for_an_arrow(key):
    """A link kind is user-authored text and may contain anything; it is still a
    KEY, and the retired guard turned such a backlog into a crash."""
    assert Subscription(key, Phase.IN_LOCK).selector == key
