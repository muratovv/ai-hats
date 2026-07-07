"""Session-dir artifact names — observe's session-dir schema (HATS-948, T15).

Filename constants + the session-dir helpers. A pure leaf (imports nothing) so
the observe writer uses it intra-package and integrator name-consumers
(retro/cli/pipeline) import it without dragging the writer. observe owns this
schema (ADR-0014); it does NOT belong in core.
"""

from __future__ import annotations

# Session directory prefix for session IDs
SESSION_PREFIX = "session_"

# Artifact file names
TRACE_LOG = "trace.log"
AUDIT_MD = "audit.md"
TRANSCRIPT_TXT = "transcript.txt"
METRICS_JSON = "metrics.json"
USAGE_JSON = "usage.json"
META_PROMPT_TXT = "meta_prompt.txt"
REASONING_LOG = "reasoning.log"
PTY_RAW_LOG = "pty_raw.log"
RETRO_LOG = "retro.log"


def session_dirname(session_id: str) -> str:
    """Return normalized session directory name for a session ID."""
    return f"{SESSION_PREFIX}{session_id}"


def strip_session_prefix(session_id: str) -> str:
    """Strip session prefix if present; idempotent."""
    if session_id.startswith(SESSION_PREFIX):
        return session_id[len(SESSION_PREFIX) :]
    return session_id


__all__ = [
    "SESSION_PREFIX",
    "TRACE_LOG",
    "AUDIT_MD",
    "TRANSCRIPT_TXT",
    "METRICS_JSON",
    "USAGE_JSON",
    "META_PROMPT_TXT",
    "REASONING_LOG",
    "PTY_RAW_LOG",
    "RETRO_LOG",
    "session_dirname",
    "strip_session_prefix",
]
