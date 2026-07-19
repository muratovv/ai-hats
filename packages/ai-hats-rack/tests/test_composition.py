"""Composition-root contract (HATS-1043 R7/R8, ADR-0017 §3-§4): the uniform
bind loop and the fail-closed ``requires_states`` validation both roots share."""

from __future__ import annotations

import pytest

from ai_hats_rack.composition import (
    HandlerProtocolError,
    UnknownHandlerError,
    build_extensions,
)
from ai_hats_rack.definition import load_backlog
from ai_hats_rack.dispatch import RequiresStatesError, bind_subscribers, validate_requires_states
from ai_hats_rack.extensions import PlanGateExtension, PlanScaffoldExtension
from ai_hats_rack.fsm import load_topology


def _defn_with_extensions(tmp_path, ext_line):
    doc = tmp_path / "backlog.yaml"
    doc.write_text(
        "name: b\nprefix: B\n"
        f"extensions: {ext_line}\n"
        "fsm:\n"
        "  initial: brainstorm\n"
        "  states: [{name: brainstorm}, {name: document}]\n"
        "  edges: [{from: brainstorm, to: document}, {from: document, to: brainstorm}]\n"
        "links:\n  kinds: [{name: parent_task}]\n"
    )
    return load_backlog(doc)


class _Stateful:
    name = "stateful"

    def __init__(self, required):
        self._required = frozenset(required)

    def subscriptions(self):
        return []

    def on_event(self, ctx):
        return None

    def requires_states(self):
        return self._required


class _Bindable:
    name = "bindable"

    def __init__(self):
        self.bound = None

    def subscriptions(self):
        return []

    def on_event(self, ctx):
        return None

    def bind(self, kernel):
        self.bound = kernel


class _Plain:
    name = "plain"

    def subscriptions(self):
        return []

    def on_event(self, ctx):
        return None


# ----- requires_states validation (R8) ---------------------------------------


def test_validate_requires_states_accepts_a_subset():
    topo = load_topology()
    # a subscriber declaring nothing and one declaring in-topology states both pass
    validate_requires_states([_Stateful(["plan", "execute"]), _Plain()], topo, source="pkg")


def test_validate_requires_states_fails_closed_naming_subscriber_and_missing():
    topo = load_topology()
    with pytest.raises(RequiresStatesError) as exc_info:
        validate_requires_states(
            [_Stateful(["plan", "qa", "shipping"])], topo, source="catalog/backlog.yaml"
        )
    err = exc_info.value
    assert err.subscriber == "stateful"
    assert err.missing == ("qa", "shipping")  # sorted, only the absent states
    assert err.source == "catalog/backlog.yaml"
    assert "shipping" in str(err) and "catalog/backlog.yaml" in str(err)


def test_stock_plan_extensions_declare_the_states_they_gate(tmp_path):
    assert PlanScaffoldExtension(tmp_path).requires_states() == frozenset({"plan"})
    assert PlanGateExtension(tmp_path).requires_states() == frozenset({"execute"})


# ----- uniform bind loop (R7) ------------------------------------------------


def test_bind_subscribers_binds_only_those_exposing_bind():
    bindable, plain = _Bindable(), _Plain()
    sentinel = object()
    bind_subscribers([bindable, plain, _Stateful([])], sentinel)
    assert bindable.bound is sentinel  # plain/stateful skipped — no bind, no error


# ----- open factory registry: build_extensions + fail-closed (R6) ------------


def test_build_extensions_instantiates_ambient_via_factory(tmp_path):
    defn = _defn_with_extensions(tmp_path, "[frozen-integrity, {name: views, priority: 40}]")
    seen = []

    def factory(d, catalog, cfg):
        seen.append((catalog, dict(cfg)))
        return _Plain()

    subs = build_extensions(defn, tmp_path, {"frozen-integrity": factory, "views": factory})
    assert len(subs) == 2  # one subscriber per ambient reference
    assert seen[0] == (tmp_path, {})  # frozen-integrity: bare name → empty config
    assert seen[1] == (tmp_path, {})  # priority is pulled out, not passed as config


def test_build_extensions_unknown_name_fails_closed_naming_it(tmp_path):
    defn = _defn_with_extensions(tmp_path, "[frozen-integrity, no-such-handler]")
    with pytest.raises(UnknownHandlerError) as exc_info:
        build_extensions(defn, tmp_path, {"frozen-integrity": lambda d, c, cfg: _Plain()})
    err = exc_info.value
    assert err.handler == "no-such-handler"
    assert "no-such-handler" in str(err)


def test_unknown_handler_error_is_a_config_error(tmp_path):
    from ai_hats_rack.errors import RackConfigError

    assert issubclass(UnknownHandlerError, RackConfigError)


# ----- protocol validation at composition (supervisor #3.1) ------------------


def test_build_extensions_rejects_a_non_subscriber_factory(tmp_path):
    # A factory that returns a plain object → fail-closed, naming the handler.
    defn = _defn_with_extensions(tmp_path, "[views]")
    with pytest.raises(HandlerProtocolError) as exc_info:
        build_extensions(defn, tmp_path, {"views": lambda d, c, cfg: object()})
    err = exc_info.value
    assert err.handler == "views"
    assert "views" in str(err) and "Subscriber" in str(err)


def test_build_extensions_accepts_a_duck_typed_subscriber(tmp_path):
    # A conforming class (name + subscriptions + on_event, NO inheritance) passes.
    defn = _defn_with_extensions(tmp_path, "[views]")
    subs = build_extensions(defn, tmp_path, {"views": lambda d, c, cfg: _Plain()})
    assert [s.name for s in subs] == ["plain"]


def test_handler_protocol_error_is_a_config_error():
    from ai_hats_rack.errors import RackConfigError

    assert issubclass(HandlerProtocolError, RackConfigError)
