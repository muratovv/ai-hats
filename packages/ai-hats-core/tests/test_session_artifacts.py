"""HATS-948 (T15) — session-dir naming vocabulary lives in core.

RED-under-revert: moving these names back out of ``ai_hats_core`` (or dropping one
from ``__all__``) fails the import + the ``ai_hats.paths`` shim-parity assertion.
"""

from __future__ import annotations

from ai_hats_core.session_artifacts import (
    METRICS_JSON,
    SESSION_PREFIX,
    session_dirname,
    strip_session_prefix,
)


def test_constants_and_helpers() -> None:
    assert METRICS_JSON == "metrics.json"
    assert session_dirname("20260707-1") == f"{SESSION_PREFIX}20260707-1"
    assert strip_session_prefix(session_dirname("x")) == "x"
    assert strip_session_prefix("x") == "x"  # idempotent


def test_public_surface() -> None:
    import ai_hats_core.session_artifacts as sa

    assert {"TRACE_LOG", "AUDIT_MD", "USAGE_JSON", "session_dirname"} <= set(sa.__all__)
