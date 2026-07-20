"""HATS-1042: unified ``backlog.yaml`` loader.

Pins the packaged tasks default as the golden states/edges/kinds fold
(``fsm.yaml`` + ``links.yaml`` retired), plus the fail-closed contract: a key
the loader does not materialize is a typed error naming the key, never a
silent no-op.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import (
    BacklogDefinition,
    BacklogDefinitionError,
    FieldSpec,
    HandlerRef,
    LegacyLinksOverrideError,
    UnsupportedBacklogKeyError,
    load_backlog,
    resolve_definition,
)
from ai_hats_rack.models import TaskCard

# Golden pin: the tasks topology, exact ai-hats-tracker parity (the former
# fsm.yaml content, now the fsm section of the packaged backlog.yaml).
_GOLDEN_EDGES = {
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


# ----- packaged default: the golden tasks fold -------------------------------


def test_packaged_backlog_is_a_definition():
    defn = load_backlog()
    assert isinstance(defn, BacklogDefinition)
    assert defn.name == "tasks"
    assert defn.prefix == "HATS"


def test_topology_matches_the_golden_fold():
    defn = load_backlog()
    assert defn.topology.initial == "brainstorm"
    assert dict(defn.topology.edges) == _GOLDEN_EDGES
    assert set(defn.topology.states) == set(_GOLDEN_EDGES)
    assert len(defn.topology.states) == 9


def test_links_match_the_golden_kinds():
    defn = load_backlog()
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


# ----- fail-closed: an unsupported key names itself, never a silent no-op -----

_SKELETON = (
    "name: t\nprefix: T\n"
    "fsm:\n"
    "  initial: brainstorm\n"
    "  states:\n{states}"
    "  edges:\n{edges}"
    "links:\n"
    "  kinds:\n{kinds}"
)


def _skeleton(
    *, states="    - {name: brainstorm}\n", edges="    []\n", kinds="    - {name: parent_task}\n"
):
    return _SKELETON.format(states=states, edges=edges, kinds=kinds)


_FAIL_CASES = {
    # an unknown kind key fails closed naming it (kind `targets` LANDED in 1044).
    "kind_generic": (
        _skeleton(kinds="    - {name: parent_task, bogus: 1}\n"),
        "bogus",
    ),
    # an unknown field-entry key fails closed naming it (HATS-1035 grammar).
    "field_key": (
        _skeleton() + "fields:\n    - {name: note, type: str, coerce: true}\n",
        "coerce",
    ),
    # arbitrary unknown key
    "top_generic": ("name: t\nprefix: T\nquux: 1\n", "quux"),
}


@pytest.mark.parametrize("text, key", list(_FAIL_CASES.values()), ids=list(_FAIL_CASES))
def test_unsupported_key_fails_closed_naming_the_key(tmp_path, text, key):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(text)
    with pytest.raises(UnsupportedBacklogKeyError) as exc_info:
        load_backlog(doc)
    err = exc_info.value
    assert err.key == key
    assert key in str(err)


def test_unsupported_key_error_is_a_config_error(tmp_path):
    # Routed to the RackConfigError subtree → the CLI "internal" marker.
    doc = tmp_path / "backlog.yaml"
    doc.write_text("name: t\nprefix: T\nquux: 1\n")
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)


# ----- binding slots parse into the immutable Bindings surface (HATS-1043) -----

_BINDINGS_DOC = (
    "name: b\nprefix: B\n"
    "extensions: [frozen-integrity, {name: derived-views, priority: 40}]\n"
    "fsm:\n"
    "  initial: plan\n"
    "  states:\n"
    "    - {name: plan, on_enter: [plan-scaffold]}\n"
    "    - {name: execute, on_enter: [{name: plan-gate, priority: 10}], on_exit: [release]}\n"
    "    - {name: document, on_enter: [{name: stamp-lifecycle, field: closed, priority: 12}]}\n"
    "  edges:\n"
    "    - {from: plan, to: execute}\n"
    "    - {from: execute, to: document}\n"
    "    - {from: document, to: execute, name: reopen, skip: [plan-gate], "
    "handlers: [clear-lifecycle]}\n"
    "links:\n"
    "  kinds:\n"
    "    - {name: depends_on, arity: many, handlers: [cycle-check]}\n"
)


def _bindings_defn(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_BINDINGS_DOC)
    return load_backlog(doc).bindings


def test_state_on_enter_and_on_exit_parse_as_refs(tmp_path):
    b = _bindings_defn(tmp_path)
    assert b.state_on_enter["plan"] == (HandlerRef("plan-scaffold"),)
    assert b.state_on_enter["execute"] == (HandlerRef("plan-gate", priority=10),)
    assert b.state_on_exit["execute"] == (HandlerRef("release"),)


def test_handler_ref_config_and_priority_split(tmp_path):
    b = _bindings_defn(tmp_path)
    ref = b.state_on_enter["document"][0]
    assert ref.name == "stamp-lifecycle"
    assert ref.priority == 12
    assert dict(ref.config) == {"field": "closed"}  # priority pulled out, config kept


def test_edge_handlers_and_skip_parse(tmp_path):
    b = _bindings_defn(tmp_path)
    assert b.edge_handlers[("document", "execute")] == (HandlerRef("clear-lifecycle"),)
    assert b.edge_skips[("document", "execute")] == frozenset({"plan-gate"})


def test_kind_handlers_parse_but_no_link_dispatch_yet(tmp_path):
    # HATS-1043 step 3 PARSES kinds[].handlers; subscriptions are step 6.
    b = _bindings_defn(tmp_path)
    assert b.kind_handlers["depends_on"] == (HandlerRef("cycle-check"),)


def test_top_level_extensions_parse_as_refs(tmp_path):
    b = _bindings_defn(tmp_path)
    assert b.extensions == (
        HandlerRef("frozen-integrity"),
        HandlerRef("derived-views", priority=40),
    )


def test_packaged_default_declares_the_migrated_kit(tmp_path):
    # HATS-1043 step 5: the packaged tasks default declares its kit — scaffold/
    # gate/stamp on entry, clear+skip on reopen, frozen-integrity ambient.
    b = load_backlog().bindings
    assert b.state_on_enter["plan"] == (HandlerRef("plan-scaffold", priority=30),)
    assert b.state_on_enter["execute"] == (HandlerRef("plan-gate", priority=10),)
    assert b.state_on_enter["done"] == (HandlerRef("stamp-lifecycle", priority=12),)
    assert b.state_on_enter["cancelled"] == (HandlerRef("stamp-lifecycle", priority=12),)
    assert b.edge_handlers[("done", "execute")] == (HandlerRef("clear-lifecycle", priority=12),)
    assert b.edge_skips[("done", "execute")] == frozenset({"plan-gate"})
    assert b.extensions == (HandlerRef("frozen-integrity"),)


def test_bad_handler_ref_shape_fails_closed(tmp_path):
    # A non-name, non-mapping handler ref is a typed load error, not a silent no-op.
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_skeleton(states="    - {name: brainstorm, on_enter: [42]}\n"))
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)


# ----- card-field schema (HATS-1035 step 1) ----------------------------------


def test_packaged_fields_are_todays_ten():
    defn = load_backlog()
    assert [f.name for f in defn.fields] == [
        "description", "priority", "assignee", "reviewer", "role",
        "tags", "resolution", "completed_at", "final_state", "work_policy",
    ]


def test_packaged_field_grammar_details():
    by_name = {f.name: f for f in load_backlog().fields}
    assert by_name["priority"].choices == ("low", "medium", "high", "critical")
    assert by_name["tags"].type == "list" and by_name["tags"].default == []
    # emit is three-layer: always by default, when-set on the lifecycle fields.
    assert by_name["description"].emit == "always"
    for name in ("resolution", "completed_at", "final_state"):
        assert by_name[name].emit == "when-set"
    # no packaged validators (first arrive with HYP in HATS-1044).
    assert all(f.validator is None for f in load_backlog().fields)


def test_schema_defaults_equal_taskcard_defaults():
    # The lossless / parity pin (R5): schema defaults ≡ TaskCard field defaults,
    # so the triple-default chain (Click → kernel → model) cannot drift.
    defn = load_backlog()
    assert len(defn.fields) == 10
    for f in defn.fields:
        model_default = TaskCard.model_fields[f.name].get_default(call_default_factory=True)
        assert f.has_default, f.name
        assert f.default == model_default, f.name


def test_packaged_extras_policy_is_allow():
    # Default = allow = today's passthrough; the packaged file omits the key.
    assert load_backlog().extras_policy == "allow"


def _fields_doc(fields_block="", extras_line=""):
    return (
        "name: t\nprefix: T\n" + extras_line +
        "fsm:\n  initial: brainstorm\n  states: [{name: brainstorm}, {name: document}]\n"
        "  edges: [{from: brainstorm, to: document}, {from: document, to: brainstorm}]\n"
        "links:\n  kinds: [{name: parent_task}]\n" + fields_block
    )


def test_custom_fields_parse_all_grammar_keys(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_fields_doc(
        "fields:\n"
        "  - {name: hypothesis, type: str, required: true}\n"
        "  - {name: votes, type: any, validator: prop-vote-entries, default: []}\n"
        "  - {name: count, type: int, default: 4}\n"
    ))
    fields = load_backlog(doc).fields
    assert fields[0] == FieldSpec(name="hypothesis", type="str", required=True)
    assert fields[1] == FieldSpec(
        name="votes", type="any", has_default=True, default=[], validator="prop-vote-entries"
    )
    assert fields[2] == FieldSpec(name="count", type="int", has_default=True, default=4)


def test_extras_forbid_parses(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_fields_doc(extras_line="extras: forbid\n"))
    assert load_backlog(doc).extras_policy == "forbid"


def test_bad_extras_value_fails_closed(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_fields_doc(extras_line="extras: sometimes\n"))
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)


def test_bad_field_type_fails_closed(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_fields_doc("fields:\n  - {name: n, type: float}\n"))
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)


def test_duplicate_field_name_fails_closed(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(_fields_doc("fields:\n  - {name: n, type: str}\n  - {name: n, type: int}\n"))
    with pytest.raises(BacklogDefinitionError):
        load_backlog(doc)


# ----- instance resolution + prefix precedence (ADR-0017 §1) ------------------


def _write_catalog_backlog(catalog, prefix="CUS"):
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "backlog.yaml").write_text(
        f"name: custom\nprefix: {prefix}\n"
        "fsm:\n"
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges:\n"
        "    - {from: brainstorm, to: document, name: advance}\n"
        "    - {from: document, to: brainstorm}\n"
        "links:\n"
        "  kinds: [{name: parent_task, inverse: children}, "
        "{name: children, derived: true, inverse: parent_task}]\n"
    )
    return catalog


def test_resolve_catalog_file_is_used_whole_prefix_authoritative(tmp_path):
    # A catalog holding backlog.yaml uses that file; its prefix wins over the
    # ai-hats.yaml task_prefix alias.
    catalog = _write_catalog_backlog(tmp_path / "tasks", prefix="CUS")
    defn = resolve_definition(catalog, prefix_alias="ALIAS")
    assert defn.name == "custom"
    assert defn.prefix == "CUS"
    assert defn.topology.states == ("brainstorm", "document")
    assert dict(defn.edge_names) == {("brainstorm", "document"): "advance"}


def test_resolve_no_file_applies_prefix_alias(tmp_path):
    # No catalog file → packaged default; the deprecated task_prefix alias
    # overrides the packaged prefix (today's zero-config behavior).
    defn = resolve_definition(tmp_path / "tasks", prefix_alias="SBX")
    assert defn.name == "tasks"
    assert defn.prefix == "SBX"
    assert defn.topology.states == load_backlog().topology.states


def test_resolve_no_file_no_alias_falls_back_to_packaged_prefix(tmp_path):
    defn = resolve_definition(tmp_path / "tasks")
    assert defn.prefix == "HATS"  # packaged default == DEFAULT_PREFIX


def test_resolve_rejects_legacy_project_root_links_yaml(tmp_path):
    # R6 (ADR-0017 §1): a project-root links.yaml is retired — fold it into
    # backlog.yaml. resolve_definition fails closed for reads AND transitions.
    (tmp_path / "links.yaml").write_text("kinds:\n  - {name: x}\n")
    with pytest.raises(LegacyLinksOverrideError):
        resolve_definition(tmp_path / "tasks", project_dir=tmp_path)


def test_resolve_without_project_links_yaml_is_packaged(tmp_path):
    # No project-root links.yaml → the packaged default, no R6 trip.
    defn = resolve_definition(tmp_path / "tasks", project_dir=tmp_path)
    assert defn.links_registry.hierarchy_kind.name == "parent_task"
