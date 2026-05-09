"""Open registry for pipeline steps.

Any module can register a Step factory under a string ID. The YAML loader
resolves ``id`` fields against this registry to instantiate steps with
their declared params.

The registry is module-level mutable state (single source per process).
Built-in steps register themselves at ``ai_hats.pipeline.steps`` import.
Third-party extensions just call ``register(name, factory)`` from their
own import-time code.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .step import Step


StepFactory = Callable[[Mapping[str, Any]], Step]

_REGISTRY: dict[str, StepFactory] = {}


class StepRegistryError(KeyError):
    """Raised when a step name is unknown or already registered."""


def register(name: str, factory: StepFactory) -> None:
    if name in _REGISTRY:
        raise StepRegistryError(f"step already registered: {name!r}")
    _REGISTRY[name] = factory


def get(name: str) -> StepFactory:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise StepRegistryError(
            f"unknown step: {name!r}. Registered: {sorted(_REGISTRY)}"
        ) from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def _reset_for_tests() -> None:
    """Clear the registry. Tests use this between runs to isolate state."""
    _REGISTRY.clear()
