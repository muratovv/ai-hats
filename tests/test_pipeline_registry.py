"""Tests for pipeline.registry — open lookup with build-time validation."""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from ai_hats.pipeline import registry as reg
from ai_hats.pipeline.step import Step, StepIO


class _Dummy(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="dummy")

    def run(self, **inputs: Any) -> dict[str, Any]:
        return {}


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry — built-ins also re-register."""
    saved = dict(reg._REGISTRY)
    reg._reset_for_tests()
    yield
    reg._reset_for_tests()
    reg._REGISTRY.update(saved)


def test_register_and_get():
    def factory(_: Mapping[str, Any]) -> Step:
        return _Dummy()

    reg.register("dummy", factory)
    assert "dummy" in reg.names()
    step = reg.get("dummy")({})
    assert isinstance(step, _Dummy)


def test_double_register_raises():
    def factory(_: Mapping[str, Any]) -> Step:
        return _Dummy()

    reg.register("dummy", factory)
    with pytest.raises(reg.StepRegistryError, match="already registered"):
        reg.register("dummy", factory)


def test_unknown_lookup_raises():
    with pytest.raises(reg.StepRegistryError, match="unknown step"):
        reg.get("nope")


def test_names_returns_sorted():
    reg.register("zeta", lambda _: _Dummy())
    reg.register("alpha", lambda _: _Dummy())
    assert reg.names() == ["alpha", "zeta"]
