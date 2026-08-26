"""Surfaces — the area's only entrance (ADR-0026 D5/D14, HATS-1826).

A surface is one way of running an agent. Every implementation answers the same
contract and none of them is named here: a caller that needs *a* surface asks
``ai_hats.surface_registry.get_surface``, and a caller that needs *the* contract —
to implement it, or to type a parameter — imports it from this facade.

Importing a name from under this package instead of from here is what
``tests/test_area_boundary.py`` refuses.
"""

from __future__ import annotations

from .contract import (
    Surface,
    SurfaceHint,
    SurfaceRunResult,
    SubagentEngine,
    TranscriptResolver,
)
from .managed_tags import sweep_stale_managed_tags

# comment-length: allow — an alias has to say what it does NOT cover
# HATS-1826: deprecated aliases, so an out-of-tree surface written against
# `Provider` keeps importing (the `LaunchProvider` precedent). Names only — a
# subclass overriding `provider_hints` is no longer called, which is a real
# break the CHANGELOG names.
Provider = Surface
ProviderHint = SurfaceHint
ProviderRunResult = SurfaceRunResult

__all__ = [
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
