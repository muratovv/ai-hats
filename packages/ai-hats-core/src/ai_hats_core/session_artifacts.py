"""Session-dir artifact names — the shared naming vocabulary (HATS-917, T15).

Domain-agnostic filename constants + the session-dir helpers used by the observe
writer and the retro/cli consumers. Promoted to core so ``ai_hats_observe`` and
the integrator share one home for these names (ADR-0014 Phase 1).
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
