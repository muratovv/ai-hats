"""Standalone session-logging engine (ADR-0014 Phase 1, T15).

The observability core extracted from the ``ai_hats`` integrator: session
lifecycle, a versioned trace/audit writer, and a surface-agnostic ``AuditWriter``
fed by a pluggable ``TranscriptParser`` adapter. Imports only ``ai_hats_core`` +
stdlib, never the integrator; ``__all__`` is the standalone public surface.

Scaffold (0.1.0 slice 1) — domain modules + exports land in later slices.
"""

from __future__ import annotations

__all__: list[str] = []
