"""fsm.yaml topology pins: exact tracker parity + structural validation."""

from __future__ import annotations

import pytest

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
    "review": ("done", "failed", "cancelled"),
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


def _write_topology(tmp_path, text):
    path = tmp_path / "fsm.yaml"
    path.write_text(text)
    return path


def test_document_state_is_required(tmp_path):
    # PROP-012: accepted obligations anchor to `document`; a topology without
    # it must not load.
    path = _write_topology(
        tmp_path,
        """
initial: brainstorm
states: [brainstorm, done]
edges:
  brainstorm: [done]
  done: []
""",
    )
    with pytest.raises(TopologyError, match="document"):
        load_topology(path)


def test_edges_must_cover_every_state(tmp_path):
    path = _write_topology(
        tmp_path,
        """
initial: brainstorm
states: [brainstorm, document]
edges:
  brainstorm: [document]
""",
    )
    with pytest.raises(TopologyError, match="cover every state"):
        load_topology(path)


def test_edge_targets_must_be_declared(tmp_path):
    path = _write_topology(
        tmp_path,
        """
initial: brainstorm
states: [brainstorm, document]
edges:
  brainstorm: [shipped]
  document: []
""",
    )
    with pytest.raises(TopologyError, match="undeclared"):
        load_topology(path)


def test_initial_must_be_declared(tmp_path):
    path = _write_topology(
        tmp_path,
        """
initial: intake
states: [brainstorm, document]
edges:
  brainstorm: [document]
  document: []
""",
    )
    with pytest.raises(TopologyError, match="initial"):
        load_topology(path)
