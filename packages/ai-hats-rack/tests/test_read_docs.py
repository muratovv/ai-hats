"""read_docs migration (HATS-1064): which docs surface per kind is now declared
via ``kinds[].read_docs`` (replacing the hardcoded PARENT/LINKED_DOC_NAMES);
derived kinds surface none whatever the declaration; the value is a string list."""

from __future__ import annotations

import pytest

from ai_hats_rack.definition import load_backlog
from ai_hats_rack.linked import _doc_names_for
from ai_hats_rack.registry import LinksRegistryError


def _reg(tmp_path, doc):
    p = tmp_path / "backlog.yaml"
    p.write_text(doc)
    return load_backlog(p).links_registry


def test_packaged_read_docs_match_the_migrated_defaults():
    reg = load_backlog().links_registry
    assert _doc_names_for(reg.require("parent_task")) == ("plan.md",)
    assert _doc_names_for(reg.require("depends_on")) == ("summary.md", "retro.md")
    assert _doc_names_for(reg.require("related")) == ("summary.md", "retro.md")


def test_derived_kind_surfaces_no_docs_even_if_declared(tmp_path):
    reg = _reg(
        tmp_path,
        "name: t\nprefix: T\n"
        "fsm:\n  initial: plan\n  states: [{name: plan}]\n  edges: []\n"
        "links:\n  kinds:\n"
        "    - {name: parent_task, arity: one, inverse: children, read_docs: [plan.md]}\n"
        "    - {name: children, derived: true, inverse: parent_task, read_docs: [x.md]}\n",
    )
    assert _doc_names_for(reg.require("parent_task")) == ("plan.md",)
    assert _doc_names_for(reg.require("children")) == ()  # derived → no docs


def test_read_docs_must_be_a_string_list(tmp_path):
    with pytest.raises(LinksRegistryError):
        _reg(
            tmp_path,
            "name: t\nprefix: T\n"
            "fsm:\n  initial: plan\n  states: [{name: plan}]\n  edges: []\n"
            "links:\n  kinds:\n"
            "    - {name: parent_task, arity: one, inverse: children, read_docs: bad}\n"
            "    - {name: children, derived: true, inverse: parent_task}\n",
        )
