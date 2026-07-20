"""parent-context read enricher (HATS-1064): the pure parent-chain walk
(cycle / depth / dangling guarded), grammar composition of ``kinds[].read`` into
a ``read:<kind>`` READ subscriber, fail-closed unknown handler, and end-to-end
enrichment of ``build_context`` over the whole ancestry."""

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
from ai_hats_rack.extensions.parent_context import render_chain, walk_parent_chain
from ai_hats_rack.linked import build_context
from ai_hats_rack.models import TaskCard


# ----- the pure walk (table-testable, no kernel) ------------------------------


def _lookups(parents, cards):
    def parent_id_of(card):
        return parents.get(card.id, "")

    def get_card(cid):
        return cards.get(cid)

    return parent_id_of, get_card


def test_walk_collects_full_chain_nearest_first():
    cards = {i: TaskCard(id=i, title=i) for i in ("T-1", "T-2", "T-3")}
    parents = {"T-1": "T-2", "T-2": "T-3"}  # T-1 -> T-2 -> T-3
    pid, get = _lookups(parents, cards)
    assert [c.id for c in walk_parent_chain(cards["T-1"], pid, get)] == ["T-2", "T-3"]


def test_walk_stops_on_cycle():
    cards = {i: TaskCard(id=i, title=i) for i in ("T-1", "T-2")}
    parents = {"T-1": "T-2", "T-2": "T-1"}  # cycle
    pid, get = _lookups(parents, cards)
    # T-2 collected, then its parent T-1 is already visited → stop (no hang)
    assert [c.id for c in walk_parent_chain(cards["T-1"], pid, get)] == ["T-2"]


def test_walk_stops_on_dangling_parent():
    cards = {"T-1": TaskCard(id="T-1", title="a")}
    parents = {"T-1": "GONE"}  # parent id resolves to no card
    pid, get = _lookups(parents, cards)
    assert walk_parent_chain(cards["T-1"], pid, get) == []


def test_walk_no_parent_returns_empty():
    cards = {"T-1": TaskCard(id="T-1", title="a")}
    pid, get = _lookups({}, cards)
    assert walk_parent_chain(cards["T-1"], pid, get) == []


def test_walk_depth_cap():
    cards = {f"T-{i}": TaskCard(id=f"T-{i}", title=str(i)) for i in range(10)}
    parents = {f"T-{i}": f"T-{i + 1}" for i in range(9)}
    pid, get = _lookups(parents, cards)
    assert len(walk_parent_chain(cards["T-0"], pid, get, max_depth=3)) == 3


def test_render_chain_has_head_and_body():
    out = render_chain([TaskCard(id="T-2", title="Beta", state="execute", description="req-B")])
    assert "T-2 [execute] Beta" in out
    assert "req-B" in out


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
    assert dict(defn.bindings.kind_read_handlers)  # the read slot parsed
    subs = build_read_subscribers(defn, tmp_path, stock_factories())
    assert len(subs) == 1
    sub = subs[0]
    assert isinstance(sub, BoundReadSubscriber) and sub.name == "parent-context"
    specs = [(s.event_key, s.phase) for s in sub.subscriptions()]
    assert specs == [("read:parent_task", Phase.READ)]


def test_unknown_read_handler_fails_closed(tmp_path):
    defn = _defn(tmp_path, _DOC.replace("[parent-context]", "[no-such-reader]"))
    with pytest.raises(UnknownHandlerError):
        build_read_subscribers(defn, tmp_path, stock_factories())


# ----- end-to-end over the packaged default backlog ---------------------------


def _card(tasks_dir, task_id, **fields):
    card = TaskCard(id=task_id, **fields)
    path = tasks_dir / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)


def test_build_context_enriches_with_whole_parent_chain(tmp_path):
    tasks = tmp_path / "tasks"
    _card(tasks, "T-1", title="grandparent", description="top reqs")
    _card(tasks, "T-2", title="parent", parent_task="T-1", description="mid reqs")
    _card(tasks, "T-3", title="child", parent_task="T-2", description="leaf")
    defn = load_backlog()  # packaged default: parent_task read: [parent-context]
    subs = build_read_subscribers(defn, tasks, stock_factories())
    pkg = build_context(tasks, "T-3", registry=defn.links_registry, read_subscribers=subs)
    assert len(pkg.enrichments) == 1
    body = pkg.enrichments[0].body
    assert "T-2" in body and "mid reqs" in body  # immediate parent
    assert "T-1" in body and "top reqs" in body  # grandparent → whole chain
