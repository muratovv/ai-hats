"""A journal reader understands BOTH spellings of an edge event (HATS-1719).

The outer form of the key changed from ``edge:<from>--<to>`` to the arrow, and
records written before the change keep the old spelling forever — so a filter
that compared the string verbatim would answer for exactly one half of a
project's history, whichever half the operator did not ask about (design §1.6).
"""

from __future__ import annotations

import pytest

from ai_hats_rack.audit_view import _matches

OLD = "edge:review--done"
NEW = "review->done"


@pytest.mark.parametrize("stored", [OLD, NEW])
@pytest.mark.parametrize("asked", [OLD, NEW])
def test_either_spelling_finds_a_record_written_in_either(stored, asked):
    assert _matches({"event": stored}, asked, None, None) is True


@pytest.mark.parametrize("stored, asked", [(NEW, "review->execute"), (OLD, "edge:plan--execute")])
def test_a_different_edge_still_does_not_match(stored, asked):
    assert _matches({"event": stored}, asked, None, None) is False


def test_a_non_edge_event_is_untouched_by_the_equivalence():
    assert _matches({"event": "epicify"}, "epicify", None, None) is True
    assert _matches({"event": "epicify"}, "link:depends_on", None, None) is False
