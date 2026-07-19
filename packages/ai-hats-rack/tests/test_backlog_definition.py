"""HATS-1042: unified ``backlog.yaml`` loader.

Pins the packaged default as a LOSSLESS fold of ``fsm.yaml`` (states + edges)
and ``links.yaml`` (kinds), plus the fail-closed contract: a key not yet
materialized in HATS-1042 is a typed error naming the key AND its successor
task, never a silent no-op.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import (
    BacklogDefinition,
    BacklogDefinitionError,
    UnsupportedBacklogKeyError,
    load_backlog,
)
from ai_hats_rack.fsm import load_topology
from ai_hats_rack.registry import load_registry


# ----- packaged default: lossless fold of fsm.yaml + links.yaml --------------


def test_packaged_backlog_is_a_definition():
    defn = load_backlog()
    assert isinstance(defn, BacklogDefinition)
    assert defn.name == "tasks"
    assert defn.prefix == "HATS"


def test_topology_is_lossless_against_fsm_yaml():
    defn = load_backlog()
    fsm = load_topology()
    assert defn.topology.initial == fsm.initial
    assert defn.topology.states == fsm.states
    assert dict(defn.topology.edges) == dict(fsm.edges)
    assert len(defn.topology.states) == 9


def test_links_are_lossless_against_links_yaml():
    defn = load_backlog()
    reg = load_registry()
    assert defn.links_registry.names() == reg.names()
    assert defn.links_registry.names() == ("parent_task", "depends_on", "related", "children")
    assert defn.links_registry.hierarchy_kind.name == "parent_task"
    assert defn.links_registry.children_kind.name == "children"
    assert defn.links_registry.get("depends_on").aliases == ("depends",)
    assert defn.links_registry.get("related").symmetric is True


def test_edge_names_are_exactly_reclaim_and_reopen():
    defn = load_backlog()
    assert dict(defn.edge_names) == {
        ("execute", "execute"): "reclaim",
        ("done", "execute"): "reopen",
    }


def test_definition_is_frozen():
    defn = load_backlog()
    with pytest.raises(Exception):
        defn.name = "other"  # frozen dataclass rejects attribute assignment


def test_explicit_path_round_trips(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: hyp\nprefix: HYP\n"
        "fsm:\n"
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges:\n"
        "    - {from: brainstorm, to: document, name: advance}\n"
        "    - {from: document, to: document}\n"
        "links:\n"
        "  kinds: [{name: parent_task, inverse: children}, {name: children, derived: true, "
        "inverse: parent_task}]\n"
    )
    defn = load_backlog(doc)
    assert defn.name == "hyp"
    assert defn.prefix == "HYP"
    assert defn.edge_names == {("brainstorm", "document"): "advance"}


# ----- fail-closed: an unsupported key names itself + its successor task ------

_SKELETON = (
    "name: t\nprefix: T\n"
    "fsm:\n"
    "  initial: brainstorm\n"
    "  states:\n{states}"
    "  edges:\n{edges}"
    "links:\n"
    "  kinds:\n{kinds}"
)


def _skeleton(*, states="    - {name: brainstorm}\n", edges="    []\n", kinds="    - {name: parent_task}\n"):
    return _SKELETON.format(states=states, edges=edges, kinds=kinds)


_FAIL_CASES = {
    # top-level sections owned by successor tasks
    "top_fields": ("name: t\nprefix: T\nfields: []\n", "fields", "HATS-1035"),
    "top_extras": ("name: t\nprefix: T\nextras: forbid\n", "extras", "HATS-1035"),
    "top_extensions": ("name: t\nprefix: T\nextensions: []\n", "extensions", "HATS-1043"),
    # per-state handler slots (HATS-1043)
    "state_on_enter": (
        _skeleton(states="    - {name: brainstorm, on_enter: [plan-gate]}\n"),
        "on_enter",
        "HATS-1043",
    ),
    "state_on_exit": (
        _skeleton(states="    - {name: brainstorm, on_exit: [release]}\n"),
        "on_exit",
        "HATS-1043",
    ),
    # per-edge handler slots (HATS-1043)
    "edge_handlers": (
        _skeleton(edges="    - {from: brainstorm, to: brainstorm, handlers: [gate]}\n"),
        "handlers",
        "HATS-1043",
    ),
    "edge_skip": (
        _skeleton(edges="    - {from: brainstorm, to: brainstorm, skip: [plan-gate]}\n"),
        "skip",
        "HATS-1043",
    ),
    # per-kind slots: targets → multi-backlog (HATS-1044), handlers → HATS-1043
    "kind_targets": (
        _skeleton(kinds="    - {name: source_task, targets: tasks}\n"),
        "targets",
        "HATS-1044",
    ),
    "kind_handlers": (
        _skeleton(kinds="    - {name: parent_task, handlers: [cycle-check]}\n"),
        "handlers",
        "HATS-1043",
    ),
}


@pytest.mark.parametrize(
    "text, key, successor", list(_FAIL_CASES.values()), ids=list(_FAIL_CASES)
)
def test_unsupported_key_names_key_and_successor(tmp_path, text, key, successor):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(text)
    with pytest.raises(UnsupportedBacklogKeyError) as exc_info:
        load_backlog(doc)
    err = exc_info.value
    assert err.key == key
    assert err.successor == successor
    assert key in str(err)
    assert successor in str(err)


def test_generic_unknown_key_has_no_successor(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text("name: t\nprefix: T\nquux: 1\n")
    with pytest.raises(UnsupportedBacklogKeyError) as exc_info:
        load_backlog(doc)
    err = exc_info.value
    assert err.key == "quux"
    assert err.successor is None
    assert "quux" in str(err)


def test_unsupported_key_error_is_a_config_error(tmp_path):
    # Routed to the RackConfigError subtree → the CLI "internal" marker.
    doc = tmp_path / "backlog.yaml"
    doc.write_text("name: t\nprefix: T\nfields: []\n")
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)
