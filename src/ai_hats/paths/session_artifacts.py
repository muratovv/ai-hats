"""Single home for session-dir artifact names (HATS-917).

Session class (observe.py) and retro/cli consumers use these names
to standardize session artifact naming.
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
