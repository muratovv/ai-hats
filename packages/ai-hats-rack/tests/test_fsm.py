"""backlog.yaml topology pins: exact tracker parity + structural validation.

Structural-invariant fixtures author a full ``backlog.yaml`` through
``load_backlog`` (fsm.yaml retired, HATS-1042); the invariants live in
``fsm._validate``, reached on that path, so the pins survive the fold.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import load_backlog
from ai_hats_rack.fsm import (
    InvalidTransitionError,
    TopologyError,
    UnknownStateError,
    load_topology,
)

# The pin: exact copy of ai-hats-tracker models.py valid_transitions().
EXPECTED_EDGES = {
    "brainstorm": ("plan", "blocked", "cancelled"),
    "plan": ("execute", "blocked", "cancelled"),
    "execute": ("execute", "document", "blocked", "failed", "cancelled"),
    "document": ("review", "blocked", "cancelled"),
    "review": ("execute", "done", "failed", "cancelled"),
    "blocked": ("brainstorm", "plan", "execute", "document", "cancelled"),
    "failed": ("brainstorm", "cancelled"),
    "done": ("execute",),
    "cancelled": (),
}


@pytest.fixture(scope="module")
def topology():
    return load_topology()


def test_topology_is_exact_tracker_copy(topology):
    assert dict(topology.edges) == EXPECTED_EDGES
    assert set(topology.states) == set(EXPECTED_EDGES)
    assert len(topology.states) == 9
    assert topology.initial == "brainstorm"


def test_reclaim_reopen_and_hubs_present(topology):
    assert topology.allows("execute", "execute")  # reclaim self-loop (HATS-955)
    assert topology.allows("done", "execute")  # reopen (HATS-328)
    working = ("brainstorm", "plan", "execute", "document")
    for state in working:
        assert topology.allows(state, "blocked")  # blocked is the pause hub
    for state in ("brainstorm", "plan", "execute", "document", "review", "blocked", "failed"):
        assert topology.allows(state, "cancelled")  # administrative close
    assert topology.targets("cancelled") == ()  # the only true dead end


def test_review_to_execute_rework_edge(topology):
    # HATS-1052: review → execute is the rework loop-back (review returned WITH
    # comments). It is legal now; the other illegal edges out of review still
    # refuse, and the reported legal set carries execute.
    assert topology.allows("review", "execute")
    with pytest.raises(InvalidTransitionError) as exc_info:
        topology.guard("T-1", "review", "plan")
    assert exc_info.value.allowed == ("execute", "done", "failed", "cancelled")


def test_guard_raises_with_legal_edges(topology):
    with pytest.raises(InvalidTransitionError) as exc_info:
        topology.guard("T-1", "brainstorm", "done")
    err = exc_info.value
    assert err.allowed == ("plan", "blocked", "cancelled")
    assert "Legal edges from 'brainstorm'" in str(err)
    assert "plan" in str(err)


def test_unknown_state_names_known_states(topology):
    with pytest.raises(UnknownStateError) as exc_info:
        topology.require_state("shipping")
    assert "brainstorm" in str(exc_info.value)


def _write_backlog(tmp_path, fsm_block):
    """A full backlog.yaml with the given ``fsm:`` block and a trivially valid
    links section — so a structural failure is attributable to the topology."""
    path = tmp_path / "backlog.yaml"
    path.write_text(
        "name: t\nprefix: T\n"
        f"fsm:\n{fsm_block}"
        "links:\n  kinds:\n    - {name: parent_task}\n"
    )
    return path


def test_document_state_is_required_at_composition(tmp_path):
    # PROP-012: the `document` anchor moved from load-time fsm._validate to the
    # tasks-discipline extension's requires_states (ADR-0017 §3/§6) — a
    # document-less topology LOADS, but composing epic-automation refuses it.
    from ai_hats_rack.dispatch import RequiresStatesError, validate_requires_states
    from ai_hats_rack.extensions import EpicAutomationExtension

    path = _write_backlog(
        tmp_path,
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: done}]\n"
        "  edges: []\n",
    )
    defn = load_backlog(path)  # loads now — no load-time document anchor
    ext = EpicAutomationExtension(topology=load_topology())
    with pytest.raises(RequiresStatesError, match="document") as exc_info:
        validate_requires_states([ext], defn.topology, source=str(path))
    assert "document" in exc_info.value.missing


def test_edge_from_undeclared_state_is_rejected(tmp_path):
    # edges-cover-states: in the edge-object form the adjacency covers every
    # declared state by construction, so the surviving failure is an edge whose
    # `from` names a state the topology never declared.
    path = _write_backlog(
        tmp_path,
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges:\n    - {from: ghost, to: document}\n",
    )
    with pytest.raises(TopologyError, match="cover every state"):
        load_backlog(path)


def test_edge_targets_must_be_declared(tmp_path):
    path = _write_backlog(
        tmp_path,
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges:\n    - {from: brainstorm, to: shipped}\n",
    )
    with pytest.raises(TopologyError, match="undeclared states"):
        load_backlog(path)


def test_initial_must_be_declared(tmp_path):
    path = _write_backlog(
        tmp_path,
        "  initial: intake\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges:\n    - {from: brainstorm, to: document}\n",
    )
    with pytest.raises(TopologyError, match="initial"):
        load_backlog(path)
