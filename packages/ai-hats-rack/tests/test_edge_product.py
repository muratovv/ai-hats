"""One product of edges, and its self-loop rule (HATS-1719).

Before this the formula was written five ways with TWO rules: ``all_edge_keys``
hard-wired ``src == "execute"`` while ``composition._self_loops`` took the
DECLARED self-edges, as ADR-0017 §3 requires. On a topology whose self-loop is
named anything else the two disagreed — so in a foreign backlog nothing
subscribed from the product fired on a legal self-transition.
"""

from __future__ import annotations


from ai_hats_rack.definition import load_backlog
from ai_hats_rack.fsm import all_edges, load_topology
from ai_hats_rack.selectors import Edge


def _topology(tmp_path, initial, states, edges):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: b\nprefix: B\n"
        "fsm:\n"
        f"  initial: {initial}\n"
        f"  states: [{states}]\n"
        f"  edges: [{edges}]\n"
        "links:\n  kinds: [{name: parent_task}]\n"
    )
    return load_backlog(doc).topology


def test_declared_self_loop_is_in_the_product(tmp_path):
    """The reproduction: a self-edge named anything but ``execute``."""
    topology = _topology(
        tmp_path,
        "working",
        "{name: working}, {name: parked}",
        "{from: working, to: working}, {from: working, to: parked}, {from: parked, to: working}",
    )
    assert Edge("working", "working") in all_edges(topology)


def test_undeclared_self_loop_is_not_in_the_product(tmp_path):
    """The other half: a state without a declared self-edge gets no self key —
    including one called ``execute``, which the old formula wired in by name."""
    topology = _topology(
        tmp_path,
        "execute",
        "{name: execute}, {name: parked}",
        "{from: execute, to: parked}, {from: parked, to: execute}",
    )
    assert Edge("execute", "execute") not in all_edges(topology)


def test_shipped_tasks_topology_keeps_its_reclaim_self_loop():
    """Behaviour-preserving where it counts: ``tasks`` DECLARES execute→execute
    (``backlog.yaml`` ``name: reclaim``), so the product is unchanged."""
    product = all_edges(load_topology())
    assert Edge("execute", "execute") in product
    assert Edge("done", "done") not in product
