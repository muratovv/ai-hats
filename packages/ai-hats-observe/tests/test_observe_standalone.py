"""ADR-0014 Phase 1 (T15 / HATS-948) — standalone consumability for observe.

Proves a third party can ``from ai_hats_observe import SessionManager`` and drive
a full session (create → log → audit) on a bare directory with ``recovery=None``:
no version subsystem, no ``ai-hats.yaml``, no composition. Imports ONLY the
``ai_hats_observe`` public surface, never an ai-hats accretion.
"""

from __future__ import annotations

import json
from pathlib import Path

import ai_hats_observe as observe
from ai_hats_observe import AuditWriter, SessionManager

# The public surface a standalone consumer needs.
_STANDALONE_SURFACE = {
    "SessionManager", "Session", "SidecarTracer", "AuditWriter", "TraceEntry", "Turn",
}


def test_public_surface_is_exported() -> None:
    """RED-under-revert: dropping any name from ``__all__`` fails this."""
    assert _STANDALONE_SURFACE <= set(observe.__all__), (
        f"ai_hats_observe.__all__ missing "
        f"{sorted(_STANDALONE_SURFACE - set(observe.__all__))}"
    )


def test_session_lifecycle_on_bare_dir(tmp_path: Path) -> None:
    """create → log → finalize with recovery=None and zero project config."""
    assert not (tmp_path / "ai-hats.yaml").exists()

    mgr = SessionManager(runs_dir=tmp_path / "runs", recovery=None)
    session = mgr.create_session()  # recovery.run() is a pure no-op here
    session.log_sys("session started")
    session.init_audit(role="assistant", provider="claude")
    session.finalize_audit({"exit_code": 0, "turns": 0})

    assert session.trace_path.exists()
    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["schema_version"] == "audit/v1"
    assert metrics["exit_code"] == 0


def test_audit_build_on_bare_dir(tmp_path: Path) -> None:
    """The default (ClaudeParser) trace fallback produces a structured audit."""
    mgr = SessionManager(runs_dir=tmp_path / "runs", recovery=None)
    session = mgr.create_session()
    session.init_audit(role="assistant", provider="claude")
    session.trace_path.write_text(
        "18:15:00.000 [REQ] find the bug\n"
        "18:15:01.000 [RES] ⏺Found it in parser.py\n"
    )

    AuditWriter().build(session, jsonl_path=None)

    audit = session.audit_path.read_text()
    assert "## Turn 1" in audit
    assert "👤 find the bug" in audit
    assert "👾 Found it in parser.py" in audit
