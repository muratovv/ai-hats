"""``session backfill`` — re-derive past counters, refuse to guess (HATS-1374).

The dangerous half of a backfill is attribution: live transcript discovery falls
back to "freshest ``*.jsonl`` newer than the session start", which is correct at
teardown and wrong retroactively. A first dry run over the real project
attributed one identical ``turns=5 / tool_calls=216`` to 60 unrelated sessions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_observe.cli import _seam
from ai_hats_observe.cli.session import session

FIXTURE = Path(__file__).parent / "fixtures" / "claude_jsonl" / "three_turns_with_tool.jsonl"
SESSION_ID = "20260730-120000-1"
PROVIDER_SESSION_ID = "abc-123"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with one unmeasured session whose transcript is on disk."""
    runs = tmp_path / ".agent" / "sessions" / "runs"
    session_dir = runs / f"session_{SESSION_ID}"
    session_dir.mkdir(parents=True)
    (session_dir / METRICS_JSON).write_text(
        json.dumps({
            "role": "maintainer",
            "provider": "claude",
            "exit_code": 0,
            "measured": False,
            "flags": ["no-structured-transcript"],
            "claude_session_id": PROVIDER_SESSION_ID,
        })
    )
    transcript = tmp_path / f"{PROVIDER_SESSION_ID}.jsonl"
    shutil.copy(FIXTURE, transcript)

    monkeypatch.setattr(_seam, "_PROJECT_DIR", lambda: tmp_path)
    monkeypatch.setattr(_seam, "_RUNS_DIR", lambda _pd: runs)
    # Wide console: at the default 80 columns rich truncates the note cell, so
    # assertions on *why* a session was refused would pass on any output.
    monkeypatch.setattr(_seam, "_CONSOLE", Console(width=200))
    return tmp_path, session_dir, transcript


def _adapter(resolved: Path | None):
    """Stand in for the integrator's provider adapter."""

    def adapter(_provider):
        return (lambda _pd, _sid, provider_session_id=None: resolved), None

    return adapter


def read_metrics(session_dir) -> dict:
    return json.loads((session_dir / METRICS_JSON).read_text())


def test_dry_run_reports_recovery_without_writing(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(transcript))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "1 examined" in result.output
    assert "would rewrite" in result.output
    assert read_metrics(session_dir)["measured"] is False, "dry run must not write"
    assert "turns" not in read_metrics(session_dir)


def test_backfill_recovers_counters(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(transcript))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    m = read_metrics(session_dir)
    assert m["measured"] is True
    assert m["turns"] == 2
    assert m["tool_calls"] == 1
    assert m["tokens"]["input"] == 180
    assert m["claude_session_id"] == PROVIDER_SESSION_ID, "identity survives the rewrite"


def test_backfill_refuses_a_transcript_that_is_not_an_exact_match(project, monkeypatch):
    """The 60-session mis-attribution guard.

    A resolver that fell through to its mtime guess returns some *other*
    session's transcript. Its stem does not match the recorded provider session
    id, and a stranger's numbers are worse than an honest gap.
    """
    tmp_path, session_dir, _transcript = project
    stranger = tmp_path / "totally-different-uuid.jsonl"
    shutil.copy(FIXTURE, stranger)
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(stranger))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    assert "no exact transcript" in result.output
    m = read_metrics(session_dir)
    assert m["measured"] is False
    assert "turns" not in m


def test_backfill_refuses_when_no_provider_session_id_recorded(project, monkeypatch):
    """Without the identity link there is nothing to match against — the exact
    case of every pre-HATS-1374 HITL session."""
    tmp_path, session_dir, transcript = project
    metrics = read_metrics(session_dir)
    del metrics["claude_session_id"]
    (session_dir / METRICS_JSON).write_text(json.dumps(metrics))
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(transcript))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "no provider session id" in result.output
    assert read_metrics(session_dir)["measured"] is False


def test_backfill_keeps_trace_log(project, monkeypatch):
    """``AuditWriter.build`` deletes trace.log by default; a backfill must not.

    It is the only remaining source for surfaces whose structured transcript
    cannot be recovered at all.
    """
    tmp_path, session_dir, transcript = project
    trace = session_dir / "trace.log"
    trace.write_text("12:00:00.000 [SYS] Session started: role=maintainer\n")
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(transcript))

    CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert trace.exists(), "backfill consumed the raw trace it was meant to preserve"


def test_already_measured_sessions_are_skipped_without_force(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    (session_dir / METRICS_JSON).write_text(
        json.dumps({"provider": "claude", "measured": True, "turns": 9, "tool_calls": 3})
    )
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(transcript))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "1 already measured" in result.output
    assert read_metrics(session_dir)["turns"] == 9


def test_selection_requires_an_explicit_scope(project):
    result = CliRunner().invoke(session, ["backfill"])

    assert result.exit_code != 0
    assert "--last" in result.output
