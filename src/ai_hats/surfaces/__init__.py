"""Surfaces — the area's only entrance (ADR-0026 D5/D14, HATS-1826).

A surface is one way of running an agent. Every implementation answers the same
contract and none of them is named here: a caller that needs *a* surface asks
``ai_hats.providers.get_provider``, and a caller that needs *the* contract —
to implement it, or to type a parameter — imports it from this facade.

Importing a name from under this package instead of from here is what
``tests/test_area_boundary.py`` refuses.
"""

from __future__ import annotations

from .contract import (
    Provider,
    ProviderHint,
    ProviderRunResult,
    SubagentEngine,
    TranscriptResolver,
)
from .managed_tags import sweep_stale_managed_tags

__all__ = [
    "Provider",
    "ProviderHint",
    "ProviderRunResult",
    "SubagentEngine",
    "TranscriptResolver",
    "sweep_stale_managed_tags",
]
