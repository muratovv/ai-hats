"""Subscription builder (HATS-1043 step 4, ADR-0017 §3): the loader expands
declaration-bound handlers over the FULL edge product (forced non-topology
entries included, HATS-518), honors ``edges[].skip``, and assigns positional
bands vs explicit pins into one numeric order. Plus the stock lifecycle stamps.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.composition import (
    build_bound_subscribers,
    compose_subscribers,
    stock_factories,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import Phase

from rack_testkit import make_kernel


def _defn(catalog: Path, body: str):
    catalog.mkdir(parents=True, exist_ok=True)
    doc = catalog / "backlog.yaml"
    doc.write_text(body)
    return load_backlog(doc)


def _stub_factory(name):
    class _Stub:
        def __init__(self):
            self.name = name
            self.PHASE = Phase.IN_LOCK

        def on_event(self, ctx):
            return None

    return lambda defn, catalog, cfg: _Stub()


def _factories():
    # stock + a stub for the made-up `scribe` on_exit handler used below.
    return {**stock_factories(), "scribe": _stub_factory("scribe")}


# A compact backlog with a self-loop (reclaim), a skipped reopen edge, and a
# forced-only target state (`shipped`, no incoming edge — reached only by force).
_DOC = (
    "name: b\nprefix: B\n"
    "extensions: [frozen-integrity]\n"
    "fsm:\n"
    "  initial: plan\n"
    "  states:\n"
    "    - {name: plan, on_exit: [scribe]}\n"
    "    - {name: execute, on_enter: [{name: plan-gate, priority: 10}]}\n"
    "    - {name: document}\n"
    "    - {name: done, on_enter: [stamp-lifecycle]}\n"
    "    - {name: shipped, on_enter: [stamp-lifecycle]}\n"
    "  edges:\n"
    "    - {from: plan, to: execute}\n"
    "    - {from: execute, to: execute, name: reclaim}\n"
    "    - {from: execute, to: document}\n"
    "    - {from: document, to: done}\n"
    "    - {from: done, to: execute, name: reopen, skip: [plan-gate], "
    "handlers: [clear-lifecycle]}\n"
    "links:\n"
    "  kinds: [{name: parent_task, inverse: children}, "
    "{name: children, derived: true, inverse: parent_task}]\n"
)

_STATES = ("plan", "execute", "document", "done", "shipped")


def _keys(sub) -> set[str]:
    return {s.event_key for s in sub.subscriptions()}


def _by_name(subs):
    return {s.name: s for s in subs}


def test_on_enter_expands_over_the_full_edge_product(tmp_path):
    # plan-gate on `execute`: every edge:<src>--execute + the reclaim self-loop,
    # minus the skipped reopen edge — forced non-topology sources included.
    subs = _by_name(build_bound_subscribers(_defn(tmp_path, _DOC), tmp_path, _factories()))
    gate = _keys(subs["plan-gate"])
    assert gate == {
        "edge:plan--execute",
        "edge:document--execute",
        "edge:shipped--execute",
        "edge:execute--execute",  # declared self-loop (reclaim) IS in the product
    }
    assert "edge:done--execute" not in gate  # reopen skips plan-gate (ADR-0017 §3)


def test_self_loop_key_only_when_the_self_edge_is_declared(tmp_path):
    subs = _by_name(build_bound_subscribers(_defn(tmp_path, _DOC), tmp_path, _factories()))
    # `done`/`shipped` have no self-edge → no self-loop key in the stamp product;
    # `execute` DOES (reclaim) → plan-gate carries edge:execute--execute.
    assert "edge:done--done" not in _keys(subs["stamp-lifecycle"])
    assert "edge:shipped--shipped" not in _keys(subs["stamp-lifecycle"])
    assert "edge:execute--execute" in _keys(subs["plan-gate"])


def test_on_exit_is_the_symmetric_full_product(tmp_path):
    # `scribe` on plan.on_exit: every edge:plan--<dst> for dst != plan (no
    # plan--plan self-edge declared, so none here).
    subs = _by_name(build_bound_subscribers(_defn(tmp_path, _DOC), tmp_path, _factories()))
    assert _keys(subs["scribe"]) == {f"edge:plan--{dst}" for dst in _STATES if dst != "plan"}


def test_edge_handlers_bind_that_exact_edge_only(tmp_path):
    subs = _by_name(build_bound_subscribers(_defn(tmp_path, _DOC), tmp_path, _factories()))
    assert _keys(subs["clear-lifecycle"]) == {"edge:done--execute"}


def test_band_vs_explicit_pin_ordering(tmp_path):
    # plan-gate pinned 10; unpinned refs get a positional band (100, 110, …) in
    # on_enter → on_exit → edge-handler order.
    subs = _by_name(build_bound_subscribers(_defn(tmp_path, _DOC), tmp_path, _factories()))
    prio = {s.name: {sub.priority for sub in s.subscriptions()} for s in subs.values()}
    assert prio["plan-gate"] == {10}  # explicit pin preserved
    assert all(p >= 100 for p in prio["stamp-lifecycle"])  # unpinned → band
    assert all(p >= 100 for p in prio["scribe"])
    assert all(p >= 100 for p in prio["clear-lifecycle"])


def test_declared_handler_subscribes_once_per_event(tmp_path):
    # A handler referenced in BOTH a state's on_enter AND an edge's handlers for
    # an edge into that state collapses to ONE subscription on that edge — a
    # double-subscription would fire it twice (the migration guard).
    body = (
        "name: b\nprefix: B\n"
        "fsm:\n"
        "  initial: plan\n"
        "  states:\n"
        "    - {name: plan}\n"
        "    - {name: execute, on_enter: [guard]}\n"
        "    - {name: document}\n"
        "  edges:\n"
        "    - {from: plan, to: execute, handlers: [guard]}\n"
        "    - {from: execute, to: document}\n"
        "links:\n  kinds: [{name: parent_task}]\n"
    )
    subs = build_bound_subscribers(_defn(tmp_path, body), tmp_path, {"guard": _stub_factory("guard")})
    guard = next(s for s in subs if s.name == "guard")
    hits = [s for s in guard.subscriptions() if s.event_key == "edge:plan--execute"]
    assert len(hits) == 1


# ----- stock lifecycle stamps through a real transition ----------------------

_KIT_DOC = (
    "name: b\nprefix: T\n"
    "fsm:\n"
    "  initial: plan\n"
    "  states:\n"
    "    - {name: plan}\n"
    "    - {name: execute}\n"
    "    - {name: document}\n"
    "    - {name: shipped, on_enter: [{name: stamp-lifecycle, field: completed_at}]}\n"
    "  edges:\n"
    "    - {from: plan, to: execute}\n"
    "    - {from: execute, to: document}\n"
    "    - {from: document, to: execute}\n"
    "    - {from: shipped, to: execute}\n"
    "links:\n  kinds: [{name: parent_task}]\n"
)


def _kit_kernel(tmp_path, cwd):
    defn = _defn(tmp_path / "cat", _KIT_DOC)
    subs = compose_subscribers(defn, tmp_path / "tasks", stock_factories())
    return make_kernel(tmp_path / "tasks", topology=defn.topology, subscribers=subs)


def test_forced_non_topology_entry_fires_declared_on_enter(tmp_path, cwd):
    # `shipped` has no incoming edge; a FORCED plan→shipped fires the real
    # edge:plan--shipped key, and the declared on_enter stamp still runs
    # (HATS-518: force weakens the arrow, not the machinery).
    kernel = _kit_kernel(tmp_path, cwd)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="t")
    kernel.transition("T-1", "shipped", actor="test", caller_cwd=cwd, force=True, reason="ship it")
    assert kernel.get("T-1").completed_at  # stamp fired on the forced non-topology entry


def test_stamp_lifecycle_field_config_stamps_a_custom_field(tmp_path, cwd):
    body = _KIT_DOC.replace("field: completed_at", "field: shipped_at")
    defn = _defn(tmp_path / "cat", body)
    subs = compose_subscribers(defn, tmp_path / "tasks", stock_factories())
    kernel = make_kernel(tmp_path / "tasks", topology=defn.topology, subscribers=subs)
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="t")
    kernel.transition("T-1", "shipped", actor="test", caller_cwd=cwd, force=True, reason="ship")
    # `shipped_at` is not a typed anchor field → rides the extras passthrough.
    assert kernel.get("T-1").extras["shipped_at"]
