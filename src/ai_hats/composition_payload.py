"""CompositionPayload — integrator-composed bundle injected into bricks (HATS-865).

Brick-legal leaf: runtime machinery imports this module, so it must never
import the composition layer at runtime (TYPE_CHECKING only). Definition of
each field: docs/glossary.md → CompositionPayload.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult

if TYPE_CHECKING:
    from .hooks_manager import HooksManager
    from .providers import Provider


@dataclass(frozen=True)
class CompositionPayload:
    """One composition per execution path (ADR-0005 П1) — built by the compose
    seam (:mod:`ai_hats.composition_seam`), consumed by runners and pipeline."""

    result: CompositionResult
    provider: "Provider"
    effective_role: str
    snapshot: dict = field(default_factory=dict)
    hooks: "HooksManager | None" = None
    static_cost_analyzer: "Callable[[str], dict | None] | None" = None
    channel: str = ""
