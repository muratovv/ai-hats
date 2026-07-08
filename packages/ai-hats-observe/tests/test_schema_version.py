"""HATS-948 (T15) — the audit/metrics surface carries a ``schema_version``.

First versioned observe surface (mirrors ``usage/v1``). RED-under-revert:
dropping the stamp from ``finalize_audit`` / ``AuditWriter._write_metrics`` means
metrics.json ships unversioned and a migration seam (slice 8) has nothing to gate.
"""

from __future__ import annotations

import json

from ai_hats_observe.audit import AuditWriter
from ai_hats_observe.session import AUDIT_SCHEMA_VERSION, Session


def _session(tmp_path) -> Session:
    session_dir = tmp_path / "session_20260327-181454-1"
    session_dir.mkdir()
    return Session(session_id="20260327-181454-1", session_dir=session_dir)


def test_schema_version_is_versioned_tag() -> None:
    assert AUDIT_SCHEMA_VERSION == "audit/v1"


def test_finalize_audit_stamps_schema_version(tmp_path) -> None:
    session = _session(tmp_path)
    session.init_audit(role="assistant", provider="claude")
    session.finalize_audit({"exit_code": 0, "turns": 0})

    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["schema_version"] == AUDIT_SCHEMA_VERSION


def test_write_metrics_stamps_schema_version(tmp_path) -> None:
    session = _session(tmp_path)
    session.init_audit(role="assistant", provider="claude")
    session.trace_path.write_text(
        "18:15:00.000 [SYS] Session started\n"
        "18:15:10.000 [REQ] test request\n"
    )

    AuditWriter().build(session, jsonl_path=None)

    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["schema_version"] == AUDIT_SCHEMA_VERSION
