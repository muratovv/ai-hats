"""Session-dir artifact names — observe's session-dir schema (HATS-948, T15).

Filename constants + the session-dir helpers. A pure leaf (stdlib only) so
the observe writer uses it intra-package and integrator name-consumers
(retro/cli/pipeline) import it without dragging the writer. observe owns this
schema (ADR-0014); it does NOT belong in core.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Session directory prefix for session IDs
SESSION_PREFIX = "session_"

# Artifact file names
TRACE_LOG = "trace.log"
AUDIT_MD = "audit.md"
TRANSCRIPT_TXT = "transcript.txt"
METRICS_JSON = "metrics.json"
USAGE_JSON = "usage.json"
META_PROMPT_TXT = "meta_prompt.txt"
ROLE_MATERIALIZATION_JSON = "role_materialization.json"
REASONING_LOG = "reasoning.log"
PTY_RAW_LOG = "pty_raw.log"
RETRO_LOG = "retro.log"


# The audit/v1 ``flags`` vocabulary — why a record carries no measurement
# (HATS-1374). Same spelling the sibling ``usage/v1`` report uses.
FLAG_NO_STRUCTURED_TRANSCRIPT = "no-structured-transcript"
FLAG_SENSOR_ERROR = "sensor-error"
FLAG_NOT_FINALIZED = "not-finalized"


def is_measured(metrics: dict) -> bool:
    """Whether this record's counters are a measurement (HATS-1374).

    The read side of the audit/v1 honesty contract: a ``False`` here means
    ``turns``/``tokens``/``tool_calls`` say nothing about the session, so a
    consumer must not compare them against a threshold or report them as a
    total. Pre-HATS-1374 records carry no ``measured`` key and may hold
    fabricated zeros — a missing ``turns`` is the only tell left, so they read
    as unmeasured only in that case.
    """
    measured = metrics.get("measured")
    if isinstance(measured, bool):
        return measured
    return "turns" in metrics


def session_dirname(session_id: str) -> str:
    """Return normalized session directory name for a session ID."""
    return f"{SESSION_PREFIX}{session_id}"


def strip_session_prefix(session_id: str) -> str:
    """Strip session prefix if present; idempotent."""
    if session_id.startswith(SESSION_PREFIX):
        return session_id[len(SESSION_PREFIX) :]
    return session_id


def session_start_dt(session_id: str) -> datetime | None:
    """Parse the leading ``YYYYMMDD-HHMMSS`` as UTC; None if malformed.

    The one place that knows where a session id carries its start time, so the
    uniqueness suffix after it stays free to change (HATS-1248). Accepts a bare
    id or a ``session_<id>`` dirname. On a nested id this yields the PARENT's
    start time — long-standing behaviour every caller already relies on.
    """
    sid = strip_session_prefix(session_id)
    try:
        return datetime.strptime(sid[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


__all__ = [
    "SESSION_PREFIX",
    "TRACE_LOG",
    "AUDIT_MD",
    "TRANSCRIPT_TXT",
    "METRICS_JSON",
    "USAGE_JSON",
    "META_PROMPT_TXT",
    "ROLE_MATERIALIZATION_JSON",
    "REASONING_LOG",
    "PTY_RAW_LOG",
    "RETRO_LOG",
    "FLAG_NO_STRUCTURED_TRANSCRIPT",
    "FLAG_SENSOR_ERROR",
    "FLAG_NOT_FINALIZED",
    "is_measured",
    "session_dirname",
    "strip_session_prefix",
    "session_start_dt",
]
