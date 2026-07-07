"""Trace vocabulary + the session-id env var (HATS-948, T15).

Shared by the observe writer/sidecar and the runtime bricks: the sidecar reads
``ENV_SESSION_ID`` to know which session's ``trace.log`` to append, and tags each
line with a ``TraceTag``. Promoted to core so ``ai_hats_observe`` and the
integrator share one home (ADR-0014 Phase 1).
"""

from __future__ import annotations

# Session-id env var — the sidecar/runtime handshake for the active session.
ENV_SESSION_ID = "AI_HATS_SESSION_ID"


class TraceTag:
    """Trace-line tag vocabulary written to ``trace.log``."""

    REQ = "[REQ]"
    RES = "[RES]"
    ACT = "[ACT]"
    TOOL = "[TOOL]"
    SYS = "[SYS]"
    SUB = "[SUB]"


__all__ = ["ENV_SESSION_ID", "TraceTag"]
