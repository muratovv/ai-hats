"""HATS-1043: the packaged ``backlog-schema.yaml`` is the single language-
independent grammar authority for backlog.yaml. Pins that it declares every
structural level, that the loader's allow-sets are file-backed BY it (no drift),
that the packaged backlog.yaml self-lints against it (the seed of the external
linter the schema exists for), and that reserved keys stay fail-closed.
"""

from __future__ import annotations

from importlib import resources

import pytest
import yaml

from ai_hats_rack import definition
from ai_hats_rack.definition import UnsupportedBacklogKeyError, load_backlog

_LEVELS = ("top", "fsm", "state", "edge", "links", "kind", "field")


def _schema() -> dict:
    text = resources.files("ai_hats_rack").joinpath("backlog-schema.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _packaged_backlog() -> dict:
    text = resources.files("ai_hats_rack").joinpath("backlog.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


# ----- the grammar file declares every level ---------------------------------


def test_schema_parses_and_declares_every_level():
    schema = _schema()
    assert set(schema["keys"]) == set(_LEVELS)
    for level in _LEVELS:
        assert isinstance(schema["keys"][level], list) and schema["keys"][level]
    # handler-ref grammar + reserved keys + priority scale are all data here.
    assert schema["handler_ref"]["universal"] == ["name", "priority", "timeout"]
    assert "reserved" in schema and "priorities" in schema


def test_priority_scale_is_machine_readable_data():
    prio = _schema()["priorities"]
    assert prio["slots"]["plan-gate"] == 10
    assert prio["slots"]["stamp-lifecycle"] == 12
    assert prio["slots"]["worktree"] == 30
    assert prio["band_start"] == 100 and prio["band_step"] == 10


# ----- the loader's allow-sets are file-backed by the schema (no drift) -------


def test_loader_allow_sets_equal_the_schema_contents():
    keys = _schema()["keys"]
    assert definition._TOP_KEYS == frozenset(keys["top"])
    assert definition._FSM_KEYS == frozenset(keys["fsm"])
    assert definition._STATE_KEYS == frozenset(keys["state"])
    assert definition._EDGE_KEYS == frozenset(keys["edge"])
    assert definition._LINKS_KEYS == frozenset(keys["links"])
    assert definition._KIND_KEYS == frozenset(keys["kind"])
    assert definition._FIELD_KEYS == frozenset(keys["field"])


# ----- self-lint: the packaged backlog.yaml validates against the schema ------


def _lint_against_schema(data: dict, schema: dict) -> list[tuple[str, str]]:
    """Seed of the external linter: check every structural key in a backlog
    document against the schema's per-level allow-sets. Returns (level, key)
    violations — handler-ref config keys pass by the config-passthrough policy."""
    keys = schema["keys"]
    bad: list[tuple[str, str]] = []

    def check(mapping: object, level: str) -> None:
        if isinstance(mapping, dict):
            bad.extend((level, k) for k in mapping if k not in keys[level])

    check(data, "top")
    fsm = data.get("fsm", {})
    check(fsm, "fsm")
    for state in fsm.get("states", []) or []:
        check(state, "state")
    for edge in fsm.get("edges", []) or []:
        check(edge, "edge")
    links = data.get("links", {})
    check(links, "links")
    for kind in links.get("kinds", []) or []:
        check(kind, "kind")
    for entry in data.get("fields", []) or []:
        check(entry, "field")
    return bad


def test_packaged_backlog_self_lints_clean():
    assert _lint_against_schema(_packaged_backlog(), _schema()) == []


def test_self_lint_catches_an_unknown_key():
    # The linter is real: a key absent from a level's allow-set surfaces as a
    # violation naming it (kind `targets` LANDED in HATS-1044 — now allowed).
    schema = _schema()
    data = _packaged_backlog()
    data["links"]["kinds"][0]["bogus"] = "x"
    assert ("kind", "bogus") in _lint_against_schema(data, schema)


# ----- landed reservations are now allowed AND unknown keys still fail closed --


def test_landed_reservations_are_now_allowed_keys():
    schema = _schema()
    # fields/extras LANDED in HATS-1035, kind targets in HATS-1044 — each moved
    # from `reserved` into its level's allow-set; the reserved table is now empty.
    assert schema["reserved"] == {}
    assert "fields" in schema["keys"]["top"] and "extras" in schema["keys"]["top"]
    assert "targets" in schema["keys"]["kind"]


def test_cli_alias_is_an_allowed_top_key_that_self_lints(tmp_path):
    # The verbs layer reads a backlog's group name from `cli_alias` (HATS-1036);
    # it must be a first-class top-level key, self-linting clean and loading.
    schema = _schema()
    assert "cli_alias" in schema["keys"]["top"]
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: widgets\nprefix: WID\ncli_alias: wid\n"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: relates, arity: many}]\n"
    )
    assert _lint_against_schema(yaml.safe_load(doc.read_text()), schema) == []
    assert load_backlog(doc).cli_alias == "wid"


def test_targets_now_loads_into_the_kind(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: t\nprefix: T\n"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: source_task, arity: one, targets: tasks}]\n"
    )
    assert load_backlog(doc).links_registry.get("source_task").targets == "tasks"


def test_unknown_kind_key_still_fails_closed(tmp_path):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: t\nprefix: T\n"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: parent_task, bogus: 1}]\n"
    )
    with pytest.raises(UnsupportedBacklogKeyError) as exc_info:
        load_backlog(doc)
    assert exc_info.value.key == "bogus"
