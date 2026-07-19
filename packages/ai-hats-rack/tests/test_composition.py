"""Composition-root contract (HATS-1043 R7/R8, ADR-0017 §3-§4): the uniform
bind loop and the fail-closed ``requires_states`` validation both roots share."""

from __future__ import annotations

import pytest

from ai_hats_rack.dispatch import RequiresStatesError, bind_subscribers, validate_requires_states
from ai_hats_rack.extensions import PlanGateExtension, PlanScaffoldExtension
from ai_hats_rack.fsm import load_topology


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
