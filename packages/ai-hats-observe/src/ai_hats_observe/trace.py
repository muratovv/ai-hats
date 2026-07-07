"""Trace vocabulary + the session-id env var — observe's trace schema (HATS-948).

``TraceTag`` (trace-line tags) + ``ENV_SESSION_ID`` (the sidecar/runtime session
handshake). A pure leaf; the writer/sidecar use it intra-package. Runtime bricks
never import it — they write via ``Session.log_sys/log_sub/log_res`` on the
injected session. observe owns this schema (ADR-0014); NOT core.
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
