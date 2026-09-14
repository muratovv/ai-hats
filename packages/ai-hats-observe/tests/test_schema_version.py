"""HATS-948 (T15) — the observe surfaces carry a ``schema_version``.

First versioned observe surface (mirrors the usage report). RED-under-revert:
dropping the stamp from ``finalize_audit`` / ``AuditWriter._write_metrics`` means
metrics.json ships unversioned and a migration seam (slice 8) has nothing to gate.

HATS-1966 S5 adds the other half: the usage report moves to ``usage/v2`` and the
files already on disk are NOT migrated (R7), so the reader must keep rendering a
``usage/v1`` report it finds while showing a v2 one's new fields.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from ai_hats_observe.audit import AuditWriter
from ai_hats_observe.cli import _host
from ai_hats_observe.cli.session import _render_usage
from ai_hats_observe.session import AUDIT_SCHEMA_VERSION, Session
from ai_hats_observe.usage import SCHEMA_VERSION as USAGE_SCHEMA_VERSION


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
        "18:15:00.000 [SYS] Session started\n18:15:10.000 [REQ] test request\n"
    )

    AuditWriter().build(session, jsonl_path=None)

    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["schema_version"] == AUDIT_SCHEMA_VERSION


# --- the usage report ------------------------------------------------------

# A ``usage/v1`` report as the HATS-664 producer wrote it: no ``api_calls``, no
# ``signals``, and totals counted once per record rather than once per API call.
_V1_ON_DISK = {
    "schema_version": "usage/v1",
    "source": "old.jsonl",
    "always_on": {"first_cache_creation_input_tokens": 18204},
    "aggregates": {
        "skill_loads": {"backlog-manager": 1},
        "tool_calls": 16,
        "tool_errors": 4,
        "tool_success_rate": 0.75,
    },
    "flags": [],
}

_V2_ON_DISK = {
    **_V1_ON_DISK,
    "schema_version": "usage/v2",
    "source": "new.jsonl",
    "api_calls": 2,
    "signals": [
        {
            "obligation": "harness_must_act",
            "kind": "wait",
            "ts": "2026-06-06T10:00:02Z",
            "detail": "You've hit your monthly spend limit",
            "raw_code": "429",
            "source": "claude/jsonl",
            "retry_after": 1780000000,
        }
    ],
}


def _rendered(usage: dict, tmp_path: Path) -> str:
    """``session show``'s Usage section for a report on disk, as text."""
    usage_path = tmp_path / "usage.json"
    usage_path.write_text(json.dumps(usage))
    buffer = io.StringIO()
    previous = _host.attach(replace(_host.STANDALONE, console=Console(file=buffer, width=200)))
    try:
        _render_usage(SimpleNamespace(usage_path=usage_path))
    finally:
        _host.attach(previous)
    return buffer.getvalue()


def test_usage_schema_version_is_the_new_counting_rule() -> None:
    assert USAGE_SCHEMA_VERSION == "usage/v2"


def test_a_v1_report_on_disk_still_renders(tmp_path) -> None:
    """Historical reports are not migrated (R7), so the v2 reader must render a
    v1 file it finds — naming the version it read, and inventing nothing.

    Positive control: the test below shows the same reader DOES print
    ``api_calls`` and ``signals`` when they are present, so their absence here
    is the v1 file's shape and not a dead renderer.
    """
    out = _rendered(_V1_ON_DISK, tmp_path)

    assert "usage/v1" in out
    assert "always_on (measured): 18,204 tok" in out
    assert "backlog-manager x1" in out
    assert "tools: 16 calls, 4 err, success_rate 0.75" in out
    assert "api_calls" not in out
    assert "signals" not in out


def test_a_v2_report_renders_its_new_fields(tmp_path) -> None:
    """The two fields the schema bump added: what cost is proportional to, and
    what happened to the run."""
    out = _rendered(_V2_ON_DISK, tmp_path)

    assert "usage/v2" in out
    assert "api_calls: 2" in out
    assert "signals: wait x1" in out
    assert "blocked: wait" in out and "harness_must_act" in out
    assert "code 429" in out
    # the v1 fields keep rendering alongside them
    assert "always_on (measured): 18,204 tok" in out
