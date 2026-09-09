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

from .diagnostics import Diagnostic

from .session_artifacts import SessionPolicy

if TYPE_CHECKING:
    from .hooks_manager import HooksManager
    from .surfaces import Surface, TranscriptResolver


@dataclass(frozen=True)
class CompositionPayload:
    """One composition per execution path (ADR-0005 D1) — built by the compose
    seam (:mod:`ai_hats.composition_seam`), consumed by runners and pipeline."""

    result: CompositionResult
    provider: "Surface"
    effective_role: str
    # What the session composed, operators included. `effective_role`
    # stays the base name reports carry; a gate needs the whole expression, or a
    # check from a runtime-added trait silently never fires.
    role_expression: str = ""
    snapshot: dict = field(default_factory=dict)
    hooks: "HooksManager | None" = None
    static_cost_analyzer: "Callable[[str], dict | None] | None" = None
    channel: str = ""
    # Observe factories for make_audit (static_cost_analyzer precedent).
    session_factory: "Callable[..., object] | None" = None
    audit_writer_factory: "Callable[[], object] | None" = None
    # Provider-owned transcript path discovery (WHERE the session log
    # lives) — paired with audit_writer_factory's parser (HOW to parse it).
    transcript_resolver: "TranscriptResolver | None" = None
    # Hooks warnings from the first-run set_role side effect, routed to
    # the HITL read-hold instead of a bare pre-launch print.
    startup_warnings: tuple[str, ...] = ()
    # What the COMPOSITION itself found — carried typed, so the level
    # is the producer's word and not the banner's guess.
    diagnostics: tuple[Diagnostic, ...] = ()
    # Session policy passed down to runners
    policy: SessionPolicy = field(default_factory=SessionPolicy)
