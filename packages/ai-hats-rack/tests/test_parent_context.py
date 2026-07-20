"""parent-context read enricher (HATS-1064): the pure parent-chain walk
(cycle / depth / dangling guarded, with an inconsistency note for the agent),
section extraction (only the 'Work Policy' section travels, not
the whole parent card), grammar composition of ``kinds[].read``, fail-closed
unknown handler, and end-to-end enrichment of ``build_context``."""

from __future__ import annotations

import pytest

from ai_hats_rack.composition import (
    BoundReadSubscriber,
    UnknownHandlerError,
    build_read_subscribers,
    stock_factories,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import Phase
from ai_hats_rack.extensions.parent_context import (
    DEFAULT_SECTION,
    extract_section,
    render_chain,
    walk_parent_chain,
)
from ai_hats_rack.linked import build_context
from ai_hats_rack.models import TaskCard


def _reqs(body: str) -> str:
    return f"## {DEFAULT_SECTION}\n{body}\n"


# ----- the pure walk (table-testable, no kernel) ------------------------------


def _lookups(parents, cards):
    def parent_id_of(card):
        return parents.get(card.id, "")

    def get_card(cid):
        return cards.get(cid)

    return parent_id_of, get_card


def test_walk_collects_full_chain_nearest_first():
    cards = {i: TaskCard(id=i, title=i) for i in ("T-1", "T-2", "T-3")}
    pid, get = _lookups({"T-1": "T-2", "T-2": "T-3"}, cards)
    chain, note = walk_parent_chain(cards["T-1"], pid, get)
    assert [c.id for c in chain] == ["T-2", "T-3"]
    assert note == ""


def test_walk_cycle_stops_and_notes_inconsistency():
    cards = {i: TaskCard(id=i, title=i) for i in ("T-1", "T-2")}
    pid, get = _lookups({"T-1": "T-2", "T-2": "T-1"}, cards)
    chain, note = walk_parent_chain(cards["T-1"], pid, get)
    assert [c.id for c in chain] == ["T-2"]  # T-1 already visited → stop, no hang
    assert "cycle" in note and "inconsistent" in note


def test_walk_dangling_parent_stops_quietly():
    cards = {"T-1": TaskCard(id="T-1", title="a")}
    pid, get = _lookups({"T-1": "GONE"}, cards)
    chain, note = walk_parent_chain(cards["T-1"], pid, get)
    assert chain == [] and note == ""  # a missing parent is not flagged inconsistent


def test_walk_no_parent_returns_empty():
    cards = {"T-1": TaskCard(id="T-1", title="a")}
    pid, get = _lookups({}, cards)
    assert walk_parent_chain(cards["T-1"], pid, get) == ([], "")


def test_walk_depth_cap_notes_possible_cycle():
    cards = {f"T-{i}": TaskCard(id=f"T-{i}", title=str(i)) for i in range(10)}
    pid, get = _lookups({f"T-{i}": f"T-{i + 1}" for i in range(9)}, cards)
    chain, note = walk_parent_chain(cards["T-0"], pid, get, max_depth=3)
    assert len(chain) == 3
    assert "exceeds 3" in note


# ----- section extraction (only governance travels) ---------------------------


def test_extract_section_returns_only_that_section():
    text = "# Title\nintro\n\n## Work Policy\n1. do X\n2. do Y\n\n## Other\nnope\n"
    assert extract_section(text, "Work Policy") == "1. do X\n2. do Y"


def test_extract_section_absent_is_empty():
    assert extract_section("just a plain description", "Work Policy") == ""


def test_render_chain_skips_parents_without_the_section():
    with_reqs = TaskCard(id="T-2", title="T-2", state="execute", description=_reqs("do X"))
    without = TaskCard(id="T-3", title="T-3", description="whole epic body, no section")
    out = render_chain([with_reqs, without], DEFAULT_SECTION)
    assert "T-2 [execute]" in out and "do X" in out
    assert "T-3" not in out and "whole epic body" not in out  # skipped: nothing travels


# ----- grammar composition ----------------------------------------------------

_DOC = (
    "name: t\nprefix: T\n"
    "fsm:\n  initial: plan\n  states: [{name: plan}]\n  edges: []\n"
    "links:\n  kinds:\n"
    "    - {name: parent_task, arity: one, inverse: children, read: [parent-context]}\n"
    "    - {name: children, derived: true, inverse: parent_task}\n"
)


def _defn(tmp_path, doc=_DOC):
    p = tmp_path / "backlog.yaml"
    p.write_text(doc)
    return load_backlog(p)


def test_kinds_read_composes_a_read_subscriber(tmp_path):
    defn = _defn(tmp_path)
    assert dict(defn.bindings.kind_read_handlers)
    subs = build_read_subscribers(defn, tmp_path, stock_factories())
    assert len(subs) == 1
    sub = subs[0]
    assert isinstance(sub, BoundReadSubscriber) and sub.name == "parent-context"
    assert [(s.event_key, s.phase) for s in sub.subscriptions()] == [("read:parent_task", Phase.READ)]


def test_unknown_read_handler_fails_closed(tmp_path):
    defn = _defn(tmp_path, _DOC.replace("[parent-context]", "[no-such-reader]"))
    with pytest.raises(UnknownHandlerError):
        build_read_subscribers(defn, tmp_path, stock_factories())


# ----- end-to-end over the packaged default backlog ---------------------------


def _save(tasks_dir, card):
    path = tasks_dir / card.id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)


def test_build_context_delivers_requirements_across_the_whole_chain(tmp_path):
    tasks = tmp_path / "tasks"
    _save(tasks, TaskCard(id="T-1", title="grandparent", description=_reqs("[plan] affordance")))
    _save(tasks, TaskCard(id="T-2", title="parent", parent_task="T-1",
                          description="intro\n" + _reqs("[after execute] A/B validate")))
    _save(tasks, TaskCard(id="T-3", title="child", parent_task="T-2", description="leaf, no section"))
    defn = load_backlog()
    subs = build_read_subscribers(defn, tasks, stock_factories())
    pkg = build_context(tasks, "T-3", registry=defn.links_registry, read_subscribers=subs)
    assert len(pkg.enrichments) == 1
    body = pkg.enrichments[0].body
    assert "affordance" in body  # grandparent's requirements (whole chain)
    assert "A/B validate" in body  # parent's requirements
    assert "leaf, no section" not in body  # a card's own body never travels


def test_build_context_no_enrichment_without_a_requirements_section(tmp_path):
    tasks = tmp_path / "tasks"
    _save(tasks, TaskCard(id="T-1", title="parent", description="epic body, no section"))
    _save(tasks, TaskCard(id="T-2", title="child", parent_task="T-1", description="x"))
    defn = load_backlog()
    subs = build_read_subscribers(defn, tasks, stock_factories())
    pkg = build_context(tasks, "T-2", registry=defn.links_registry, read_subscribers=subs)
    assert pkg.enrichments == ()  # nothing to deliver → no bloat


def test_build_context_surfaces_parent_cycle_note(tmp_path):
    tasks = tmp_path / "tasks"  # a parent_task cycle constructed directly on disk
    _save(tasks, TaskCard(id="T-1", title="a", parent_task="T-2", description="x"))
    _save(tasks, TaskCard(id="T-2", title="b", parent_task="T-1", description="y"))
    defn = load_backlog()
    subs = build_read_subscribers(defn, tasks, stock_factories())
    pkg = build_context(tasks, "T-1", registry=defn.links_registry, read_subscribers=subs)
    assert len(pkg.enrichments) == 1
    assert "cycle" in pkg.enrichments[0].body and "inconsistent" in pkg.enrichments[0].body
