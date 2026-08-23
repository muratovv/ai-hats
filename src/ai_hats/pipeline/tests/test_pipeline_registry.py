"""Tests for pipeline.registry — open lookup with build-time validation."""

from __future__ import annotations

import importlib.metadata
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
    names = reg.names()
    assert names == sorted(names)
    assert {"alpha", "zeta"} <= set(names)


def test_names_lists_a_built_in_nothing_has_imported():
    """A caller asking "is this step wired?" gets the same answer either side of
    the import — declared and imported are both resolvable (HATS-1783)."""
    assert "compose_role" not in reg._REGISTRY
    assert "compose_role" in reg.names()


def test_get_resolves_a_built_in_through_its_entry_point():
    """The registry imports the declaring module, and only when the id is used."""
    assert "provider" not in reg._REGISTRY
    factory = reg.get("provider")
    assert reg._REGISTRY["provider"] is factory
    assert factory.__module__ == "ai_hats.pipeline.steps.launch"


def test_register_wins_over_the_declaration():
    """A step already registered — a user step, or one resolved earlier — is not
    re-resolved: ``get`` never reaches metadata for a name it already holds."""

    def factory(_: Mapping[str, Any]) -> Step:
        return _Dummy()

    reg.register("provider", factory)
    assert reg.get("provider") is factory


def test_no_advertised_steps_names_the_uninstalled_tree(monkeypatch):
    """The decided answer for a bare source tree: refuse, and say why.

    Entry points come from installed metadata, so a checkout with nothing
    installed resolves no built-in at all. A module-path fallback table would
    keep this running — and would be a second owner of the mapping (ADR-0026 D3),
    silently green while the declarations were missing from the wheel.
    """
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: [])
    reg._reset_for_tests()
    with pytest.raises(reg.StepRegistryError) as excinfo:
        reg.get("compose_role")
    message = str(excinfo.value)
    assert "no installed distribution advertises" in message
    assert reg.STEP_ENTRY_POINT_GROUP in message
    assert "Reinstall" in message


def test_two_distributions_claiming_one_id_is_refused(monkeypatch):
    """The group is open, so two packages can advertise the same id — and the
    resolver must not pick one quietly. ``register`` refuses the same collision."""

    class _Claim:
        def __init__(self, value: str) -> None:
            self.name = "provider"
            self.value = value

        def load(self):  # pragma: no cover — the collision is refused before load
            raise AssertionError("a contested id must not be loaded")

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **_: [_Claim("acme.steps:Theirs"), _Claim("ai_hats.pipeline.steps.launch:Provider")],
    )
    reg._reset_for_tests()
    with pytest.raises(reg.StepRegistryError) as excinfo:
        reg.get("provider")
    message = str(excinfo.value)
    assert "more than one distribution" in message
    assert "acme.steps:Theirs" in message


def test_a_broken_declaration_names_the_id_and_the_target(monkeypatch):
    """A step whose entry point will not import fails at the id, not somewhere else."""

    class _Broken:
        name = "broken"
        value = "no.such.module:Step"

        def load(self):
            raise ModuleNotFoundError("No module named 'no.such.module'")

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: [_Broken()])
    reg._reset_for_tests()
    with pytest.raises(reg.StepRegistryError) as excinfo:
        reg.get("broken")
    message = str(excinfo.value)
    assert "'broken'" in message
    assert "no.such.module:Step" in message
    assert "ModuleNotFoundError" in message
