"""Re-export shim — the observe domain now lives in ``ai_hats_observe`` (HATS-948).

Moved to ``packages/ai-hats-observe/`` (T15). Lazy integrator consumers
(composition_seam, subagent/wrap runners, harness, cli) import the writer symbols
from here; this shim re-exports them from the package (integrator → package, the
allowed direction). Drop it at parent-close once no ``ai_hats.observe`` importer
remains.
"""

from __future__ import annotations

from ai_hats_observe.audit import AuditWriter, TraceEntry, Turn
from ai_hats_observe.session import Session, SessionManager
from ai_hats_observe.sidecar import SidecarTracer

__all__ = [
    "AuditWriter",
    "Session",
    "SessionManager",
    "SidecarTracer",
    "TraceEntry",
    "Turn",
]
