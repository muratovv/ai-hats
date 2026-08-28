"""Surfaces — the area's only entrance (ADR-0026 D5/D14, HATS-1826).

A surface is one way of running an agent. Every implementation answers the same
contract and none of them is named here: a caller that needs *a* surface asks
``ai_hats.surface_registry.get_surface``, and a caller that needs *the* contract —
to implement it, or to type a parameter — imports it from this facade.

Importing a name from under this package instead of from here is what
``tests/test_area_boundary.py`` refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the names below resolve for a reader and a type checker
    from .contract import (
        MetricsSink,
        SubagentEngine,
        Surface,
        SurfaceHint,
        SurfaceRunResult,
        TranscriptResolver,
    )
    from .managed_tags import sweep_stale_managed_tags

# comment-length: allow — an alias has to say what it does NOT cover
# HATS-1826: deprecated aliases, so an out-of-tree surface written against
# `Provider` keeps importing (the `LaunchProvider` precedent). Names only — a
# subclass overriding `provider_hints` is no longer called, which is a real
# break the CHANGELOG names.
_ALIASES = {
    "Provider": "Surface",
    "ProviderHint": "SurfaceHint",
    "ProviderRunResult": "SurfaceRunResult",
}

# Bound lazily (PEP 562): the hook dispatchers live under this package and are a
# fresh process per tool call, so entering it must not cost `contract`.
_HOMES = {
    "MetricsSink": ".contract",
    "Provider": ".contract",
    "ProviderHint": ".contract",
    "ProviderRunResult": ".contract",
    "SubagentEngine": ".contract",
    "Surface": ".contract",
    "SurfaceHint": ".contract",
    "SurfaceRunResult": ".contract",
    "TranscriptResolver": ".contract",
    "sweep_stale_managed_tags": ".managed_tags",
}


def __getattr__(name: str) -> object:
    from importlib import import_module

    home = _HOMES.get(name)
    if home is not None:
        value = getattr(import_module(home, __name__), _ALIASES.get(name, name))
        globals()[name] = value  # bound once; later lookups skip __getattr__
        return value
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Eager binding used to expose the submodules this facade imported; keep it.
    try:
        return import_module(f"{__name__}.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted({*globals(), *_HOMES})


__all__ = [
    "MetricsSink",
    "Surface",
    "SurfaceHint",
    "SurfaceRunResult",
    "SubagentEngine",
    "TranscriptResolver",
    "sweep_stale_managed_tags",
    "Provider",
    "ProviderHint",
    "ProviderRunResult",
]
