"""HATS-948 (T15) — observe owns its session/trace vocab as pure leaves.

RED-under-revert: routing this vocab back through core (the reviewed-away design)
means these imports resolve elsewhere. The leaves import nothing but stdlib, so a
consumer can pull a filename/tag without dragging the writer.
"""

from __future__ import annotations

from ai_hats_observe.artifacts import (
    METRICS_JSON,
    SESSION_PREFIX,
    session_dirname,
    strip_session_prefix,
)
from ai_hats_observe.trace import ENV_SESSION_ID, TraceTag


def test_artifact_vocab() -> None:
    assert METRICS_JSON == "metrics.json"
    assert session_dirname("x") == f"{SESSION_PREFIX}x"
    assert strip_session_prefix(session_dirname("x")) == "x"


def test_trace_vocab() -> None:
    assert TraceTag.SYS == "[SYS]"
    assert TraceTag.SUB == "[SUB]"
    assert ENV_SESSION_ID == "AI_HATS_SESSION_ID"
