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

_LEVELS = ("top", "fsm", "state", "edge", "links", "kind")


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
    return bad


def test_packaged_backlog_self_lints_clean():
    assert _lint_against_schema(_packaged_backlog(), _schema()) == []


def test_self_lint_catches_an_injected_reserved_key():
    # The linter is real: a reserved key surfaces as a violation naming it.
    schema = _schema()
    data = _packaged_backlog()
    data["fields"] = []  # HATS-1035, reserved
    assert ("top", "fields") in _lint_against_schema(data, schema)


# ----- reserved keys are data AND still fail closed in the loader -------------


def test_reserved_keys_are_declared_as_data_not_in_the_allow_sets():
    schema = _schema()
    reserved = schema["reserved"]
    assert reserved["top"] == {"fields": "HATS-1035", "extras": "HATS-1035"}
    assert reserved["kind"] == {"targets": "HATS-1044"}
    # a reserved key is never also an allowed key (that would defeat fail-closed).
    assert not (set(reserved["top"]) & set(schema["keys"]["top"]))
    assert not (set(reserved["kind"]) & set(schema["keys"]["kind"]))


@pytest.mark.parametrize("text, key", [
    ("name: t\nprefix: T\nfields: []\n", "fields"),
    ("name: t\nprefix: T\nextras: forbid\n", "extras"),
])
def test_reserved_top_keys_still_fail_closed(tmp_path, text, key):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(text)
    with pytest.raises(UnsupportedBacklogKeyError) as exc_info:
        load_backlog(doc)
    assert exc_info.value.key == key
