"""``session backfill`` — re-derive past counters, refuse to guess (HATS-1374).

The dangerous half of a backfill is attribution: live transcript discovery falls
back to "freshest ``*.jsonl`` newer than the session start", which is correct at
teardown and wrong retroactively. A first dry run over the real project
attributed one identical ``turns=5 / tool_calls=216`` to 60 unrelated sessions.
"""

from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_core.layout import ProjectLayout
from ai_hats_observe.cli import _seam
from ai_hats_observe.cli.session import session

FIXTURE = Path(__file__).parent / "fixtures" / "claude_jsonl" / "three_turns_with_tool.jsonl"
SESSION_ID = "20260730-120000-1"
# UUID-shaped on purpose: the trace-recovery regex only accepts the shape
# claude actually emits, so a loose token would not exercise it.
PROVIDER_SESSION_ID = "5c639a19-5b64-4a91-8813-2937b47e9126"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with one unmeasured session whose transcript is on disk."""
    runs = tmp_path / ".agent" / "sessions" / "runs"
    session_dir = runs / f"session_{SESSION_ID}"
    session_dir.mkdir(parents=True)
    (session_dir / METRICS_JSON).write_text(
        json.dumps(
            {
                "role": "maintainer",
                "provider": "claude",
                "exit_code": 0,
                "measured": False,
                "flags": ["no-structured-transcript"],
                "claude_session_id": PROVIDER_SESSION_ID,
            }
        )
    )
    transcript = tmp_path / f"{PROVIDER_SESSION_ID}.jsonl"
    shutil.copy(FIXTURE, transcript)

    monkeypatch.setattr(
        _seam, "_LAYOUT", lambda: ProjectLayout(root=tmp_path, base=tmp_path / ".agent")
    )
    # Wide console: at the default 80 columns rich truncates the note cell, so
    # assertions on *why* a session was refused would pass on any output.
    monkeypatch.setattr(_seam, "_CONSOLE", Console(width=200))
    return tmp_path, session_dir, transcript


def _adapter(transcripts_dir: Path):
    """The integrator's provider adapter, wired to the real resolution rule.

    HATS-1397: this used to return a fixed path whatever id it was handed — a
    resolver no surface implements. Refusing a stranger is the resolver's job
    now, so a stub that cannot refuse would prove nothing about the refusal.
    """
    from ai_hats.paths import resolve_transcript

    def adapter(_provider):
        def resolver(_pd, sid, provider_session_id=None):
            return resolve_transcript(
                transcripts_dir,
                "*.jsonl",
                sid,
                exact_path=(
                    transcripts_dir / f"{provider_session_id}.jsonl"
                    if provider_session_id
                    else None
                ),
            )

        return resolver, None

    return adapter


def read_metrics(session_dir) -> dict:
    return json.loads((session_dir / METRICS_JSON).read_text())


def test_dry_run_reports_recovery_without_writing(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "1 examined" in result.output
    assert "would rewrite" in result.output
    assert read_metrics(session_dir)["measured"] is False, "dry run must not write"
    assert "turns" not in read_metrics(session_dir)


def test_backfill_recovers_counters(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    m = read_metrics(session_dir)
    assert m["measured"] is True
    assert m["turns"] == 2
    assert m["tool_calls"] == 1
    assert m["tokens"]["input"] == 180
    assert m["claude_session_id"] == PROVIDER_SESSION_ID, "identity survives the rewrite"


def test_backfill_refuses_a_stranger_when_our_transcript_is_gone(project, monkeypatch):
    """The 60-session mis-attribution guard — now enforced by the resolver itself.

    Claude expires its JSONL after ~30–40 days, so the common archive shape is
    "ours is gone, someone else's is newer". HATS-1397 moved the refusal into
    ``resolve_transcript``: a caller holding an id gets the exact file or None.
    """
    tmp_path, session_dir, transcript = project
    transcript.unlink()
    shutil.copy(FIXTURE, tmp_path / "totally-different-uuid.jsonl")
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    assert "no transcript" in result.output
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
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "no provider session id" in result.output
    assert read_metrics(session_dir)["measured"] is False


def test_identity_is_recovered_from_the_logged_launch_line(project, monkeypatch):
    """Pre-HATS-1374 records carry no id, but the runner logged the launch line.

    Measured on the real project: of 120 unmeasured sessions, 118 still had
    trace.log, 34 yielded a ``--session-id``, and 6 of those transcripts were
    still on disk. Still an exact identity — not the mtime guess.
    """
    tmp_path, session_dir, transcript = project
    metrics = read_metrics(session_dir)
    del metrics["claude_session_id"]
    (session_dir / METRICS_JSON).write_text(json.dumps(metrics))
    (session_dir / "trace.log").write_text(
        "12:00:00.000 [SYS] Session started: role=maintainer\n"
        f"12:00:00.100 [SYS] Launching: claude --settings x.json "
        f"--session-id {PROVIDER_SESSION_ID}\n"
    )
    (session_dir / METRICS_JSON).chmod(0o644)
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    assert "id from trace" in result.output
    m = read_metrics(session_dir)
    assert m["measured"] is True
    assert m["turns"] == 2
    assert m["claude_session_id"] == PROVIDER_SESSION_ID, (
        "recovered identity must be persisted — the next audit deletes the trace it came from"
    )
    assert stat.S_IMODE((session_dir / METRICS_JSON).stat().st_mode) == 0o600


def test_trace_without_a_session_id_flag_still_refuses(project, monkeypatch):
    """Fails closed: a surface whose launch line has no such flag stays refused."""
    tmp_path, session_dir, transcript = project
    metrics = read_metrics(session_dir)
    del metrics["claude_session_id"]
    (session_dir / METRICS_JSON).write_text(json.dumps(metrics))
    (session_dir / "trace.log").write_text(
        "12:00:00.100 [SYS] Launching: agy -i task list --add-dir /x/rules\n"
    )
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "no provider session id" in result.output
    assert read_metrics(session_dir)["measured"] is False


def test_a_stray_uuid_in_terminal_output_is_not_our_identity(project, monkeypatch):
    """trace.log is the whole PTY stream, not a launch record (HATS-1397, F5).

    The launch line of a ``--resume`` session carries no ``--session-id``, and
    ``[RES]`` is whatever the terminal printed afterwards. Two independent
    searches over the whole file paired this session's provider with a uuid from
    someone else's output, and the backfill persisted that forever.
    """
    tmp_path, session_dir, transcript = project
    metrics = read_metrics(session_dir)
    del metrics["claude_session_id"]
    (session_dir / METRICS_JSON).write_text(json.dumps(metrics))
    (session_dir / "trace.log").write_text(
        "12:00:00.100 [SYS] Launching: claude --settings x.json --resume\n"
        f"12:00:31.400 [RES] resuming session --session-id {PROVIDER_SESSION_ID}\n"
    )
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "no provider session id" in result.output
    m = read_metrics(session_dir)
    assert m["measured"] is False
    assert "claude_session_id" not in m, "a uuid seen in terminal output is not an identity"


def test_backfill_refuses_a_live_session(project, monkeypatch):
    """``--all`` must not rewrite a session that is still running (HATS-1397, F6).

    ``build`` replaces audit.md wholesale, so the incremental ``## Events`` log a
    live session is still appending to would be destroyed — and it races
    ``finalize_audit`` for metrics.json. ``finalized`` was added in HATS-1374 for
    exactly this question and then never asked.
    """
    tmp_path, session_dir, transcript = project
    metrics = read_metrics(session_dir)
    metrics["finalized"] = False
    (session_dir / METRICS_JSON).write_text(json.dumps(metrics))
    (session_dir / "audit.md").write_text("# Session Audit: live\n\n## Events\n\n- started\n")
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert result.exit_code == 0, result.output
    assert "not finalized" in result.output
    assert "## Events" in (session_dir / "audit.md").read_text()
    assert "turns" not in read_metrics(session_dir)


def test_backfill_keeps_trace_log(project, monkeypatch):
    """``AuditWriter.build`` deletes trace.log by default; a backfill must not.

    It is the only remaining source for surfaces whose structured transcript
    cannot be recovered at all.
    """
    tmp_path, session_dir, transcript = project
    trace = session_dir / "trace.log"
    trace.write_text("12:00:00.000 [SYS] Session started: role=maintainer\n")
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert trace.exists(), "backfill consumed the raw trace it was meant to preserve"


def test_already_measured_sessions_are_skipped_without_force(project, monkeypatch):
    tmp_path, session_dir, transcript = project
    (session_dir / METRICS_JSON).write_text(
        json.dumps({"provider": "claude", "measured": True, "turns": 9, "tool_calls": 3})
    )
    monkeypatch.setattr(_seam, "_PROVIDER_ADAPTER", _adapter(tmp_path))

    result = CliRunner().invoke(session, ["backfill", SESSION_ID])

    assert "1 already measured" in result.output
    assert read_metrics(session_dir)["turns"] == 9


def test_selection_requires_an_explicit_scope(project):
    result = CliRunner().invoke(session, ["backfill"])

    assert result.exit_code != 0
    assert "--last" in result.output
