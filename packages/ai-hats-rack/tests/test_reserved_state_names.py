"""A topology may not name a state ``ANY`` or ``NONE`` (HATS-1719, design §1.4).

Reserving a word by convention is not reserving it. The sentinel is compared by
string equality in the dispatcher, so a state actually called ``ANY`` turns
EXACT subscriptions into wildcards: every row declared for one pair starts
firing on every pair, silently and repeatedly — gates, ownership and consent
alike. The refusal has to be at LOAD, where the name enters the system.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import load_backlog
from ai_hats_rack.fsm import TopologyError


def _write(tmp_path, states, edges, initial):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: b\nprefix: B\n"
        f"fsm:\n  initial: {initial}\n  states: [{states}]\n  edges: [{edges}]\n"
        "links:\n  kinds: [{name: parent_task}]\n"
    )
    return doc


@pytest.mark.parametrize("reserved", ["ANY", "NONE"])
def test_a_topology_naming_a_reserved_state_is_refused_at_load(tmp_path, reserved):
    doc = _write(
        tmp_path,
        f"{{name: {reserved}}}, {{name: work}}",
        f"{{from: {reserved}, to: work}}, {{from: work, to: {reserved}}}",
        reserved,
    )
    with pytest.raises(TopologyError, match=reserved):
        load_backlog(doc)


def test_the_refusal_says_why_the_word_is_taken(tmp_path):
    doc = _write(
        tmp_path, "{name: ANY}, {name: work}", "{from: ANY, to: work}, {from: work, to: ANY}", "work"
    )
    with pytest.raises(TopologyError, match="selector"):
        load_backlog(doc)


def test_a_lowercase_lookalike_is_still_a_perfectly_good_state(tmp_path):
    """The reservation is on the WORD, and the case is what makes it not a name."""
    doc = _write(
        tmp_path, "{name: any}, {name: work}", "{from: any, to: work}, {from: work, to: any}", "any"
    )
    assert "any" in load_backlog(doc).topology.states
