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
# HATS-1397: the provider's structured transcript, copied in before the trace is
# dropped. audit.md is a summary; this is the source it was summarised from.
TRANSCRIPT_JSONL = "transcript.jsonl"
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
# HATS-1397: narrower than the three above — the transcript parsed fine, but this
# surface emits no usage field at all, so only the token counters are unknowable.
FLAG_NO_TOKEN_TELEMETRY = "token-telemetry-unavailable"  # noqa: S105 — LLM tokens, not a secret
# HATS-1433: the counters exist but nobody measured them — they were scraped from
# rendered output or estimated from text length, so no consumer may read them as fact.
FLAG_TOKEN_TELEMETRY_ESTIMATED = "token-telemetry-estimated"  # noqa: S105 — LLM tokens, not a secret


def has_real_counters(metrics: dict) -> bool:
    """Whether a counter here holds a value no failed parse could have invented.

    A fabricated record is all-zero by construction — HATS-1374 wrote the absence
    of a measurement as ``turns: 0`` — so one non-zero counter is proof that some
    earlier parse really measured this session, with or without the ``measured``
    key that only post-HATS-1374 records carry.
    """
    if metrics.get("turns") or metrics.get("tool_calls"):
        return True
    tokens = metrics.get("tokens")
    if isinstance(tokens, dict) and any(tokens.values()):
        return True
    models = metrics.get("models")
    return bool(isinstance(models, dict) and models)


def is_measured(metrics: dict) -> bool:
    """Whether this record's counters are a measurement (HATS-1374).

    The read side of the audit/v1 honesty contract: a ``False`` here means
    ``turns``/``tokens``/``tool_calls`` say nothing about the session, so a
    consumer must not compare them against a threshold or report them as a
    total.

    HATS-1397: pre-HATS-1374 records carry no ``measured`` key, and this used to
    read the mere presence of ``turns`` as provenance — so a fabricated
    ``turns: 0`` passed as measured (raising bogus zero-output incidents) while
    ``_write_metrics``, which trusted only ``measured: true``, erased genuine
    legacy counters. One predicate now answers for both sides.
    """
    measured = metrics.get("measured")
    if isinstance(measured, bool):
        return measured
    return has_real_counters(metrics)


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
    "FLAG_NO_TOKEN_TELEMETRY",
    "FLAG_TOKEN_TELEMETRY_ESTIMATED",
    "is_measured",
    "session_dirname",
    "strip_session_prefix",
    "session_start_dt",
]
