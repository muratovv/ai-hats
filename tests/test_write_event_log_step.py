"""Tests for the ``WriteEventLog`` pipeline step (HATS-1966).

Sibling of ``test_compute_usage_step.py``: same fixture transcript, same
fail-soft claim — but the third artifact. Every negative here (no reader, no
transcript, a write that blew up) is paired with the positive run over the SAME
fixture, so "no file" cannot pass on an empty fixture or an unfound transcript.
The resolver is handed in rather than patched: it is a funnel input, and WHERE a
surface keeps its transcripts is the compute_usage tests' subject, not this one's.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.pipeline.steps.write_event_log import WriteEventLog
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_observe.event_log import EVENT_LOG_JSONL, EVENT_SCHEMA_VERSION, read_events
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

TRANSCRIPT = Path(__file__).parent / "fixtures" / "transcripts" / "normal.jsonl"

SESSION_ID = "20260605-100000-1"
CLAUDE_SESSION_ID = "events-uuid"


def _transcript(tmp_path: Path, name: str = "transcript.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(TRANSCRIPT.read_text())
    return path


def _resolver_for(*transcripts: Path):
    """A transcript resolver in the shape the finalize funnel threads one."""

    def resolve(project_dir, session_id, *, provider_session_id=None, end_ts=None):
        del project_dir, session_id, provider_session_id, end_ts
        return list(transcripts)

    return resolve


def _session(tmp_path: Path, provider: str, *, name: str = "session_test") -> Path:
    """A finished session dir whose metrics.json names the surface that ran it."""
    session_dir = tmp_path / name
    session_dir.mkdir(parents=True)
    (session_dir / METRICS_JSON).write_text(
        json.dumps({"role": "maintainer", "provider": provider, "exit_code": 0})
    )
    return session_dir


def _run(session_dir: Path, project_dir: Path, resolver) -> dict:
    return WriteEventLog().run(
        session_id=SESSION_ID,
        session_dir=session_dir,
        claude_session_id=CLAUDE_SESSION_ID,
        layout=ProjectLayout.at(project_dir),
        transcript_resolver=resolver,
    )


def _write_run(tmp_path: Path, provider: str, *, name: str = "session_test") -> tuple[dict, Path]:
    """The whole positive path: one surface, one transcript, one step run."""
    session_dir = _session(tmp_path, provider, name=name)
    delta = _run(session_dir, tmp_path, _resolver_for(_transcript(tmp_path)))
    return delta, session_dir


# ---------------------------------------------------------------------------
# StepIO contract, and where the reader comes from
# ---------------------------------------------------------------------------


def test_io_contract():
    io = WriteEventLog().io
    assert io.name == "write_event_log"
    assert io.requires == frozenset(
        {
            "session_id",
            "session_dir",
            "claude_session_id",
            "layout",
        }
    )
    assert io.optional == frozenset({"transcript_resolver"})
    assert io.produces == frozenset({"event_log_path"})


def test_failure_policy_continue():
    """The artifact is additive — losing it must never orphan finalization."""
    assert WriteEventLog.failure_policy == "continue"


def test_the_reader_rides_the_surface():
    """Claude overrides ``event_reader``; every other surface keeps the None default."""
    assert ClaudeSurface().event_reader() is ClaudeTranscriptReader
    assert AgySurface().event_reader() is None


# ---------------------------------------------------------------------------
# Behaviour: writes events.jsonl from the resolved transcript
# ---------------------------------------------------------------------------


def test_writes_the_event_log_for_a_claude_session(tmp_path):
    """A Claude session leaves events.jsonl beside the artifacts it already writes."""
    delta, session_dir = _write_run(tmp_path, "claude")

    event_log = session_dir / EVENT_LOG_JSONL
    assert delta == {"event_log_path": event_log}
    assert event_log.exists()

    lines = event_log.read_text().splitlines()
    assert lines, "the fixture transcript produced no lines"
    assert all(json.loads(line)["v"] == EVENT_SCHEMA_VERSION for line in lines)

    kinds = [type(event).__name__ for event in read_events(event_log)]
    assert kinds.count("ResponseStarted") == kinds.count("ResponseEnded") == 1
    assert "PromptReceived" in kinds and "ToolResultReceived" in kinds


def test_the_event_log_is_a_private_artifact(tmp_path):
    """0600 like every other session artifact — ``write_events`` owns only the format."""
    _, session_dir = _write_run(tmp_path, "claude")

    mode = stat.S_IMODE((session_dir / EVENT_LOG_JSONL).stat().st_mode)
    assert mode == 0o600, f"events.jsonl is {oct(mode)}, not private"


def test_several_resolved_transcripts_all_reach_the_log(tmp_path):
    """A resolver may answer with more than one file; the later ones append.

    Positive control: the single-transcript run in the sibling test writes the
    same fixture once, so the doubled count here is the append branch working
    rather than a file written twice over itself.
    """
    session_dir = _session(tmp_path, "claude")
    two = (_transcript(tmp_path, "a.jsonl"), _transcript(tmp_path, "b.jsonl"))

    _run(session_dir, tmp_path, _resolver_for(*two))

    doubled = len((session_dir / EVENT_LOG_JSONL).read_text().splitlines())
    _, single_dir = _write_run(tmp_path, "claude", name="single")
    single = len((single_dir / EVENT_LOG_JSONL).read_text().splitlines())
    assert doubled == 2 * single, f"{doubled} lines from two copies of a {single}-line log"


def test_no_op_for_a_surface_with_no_event_reader(tmp_path):
    """agy has no canonical reading yet, so its session gets no events.jsonl.

    Positive control: the SAME fixture, resolver and session shape DO write the
    file under `claude` — so an absent file here is the surface's answer, not an
    empty fixture or a transcript nobody found.
    """
    delta, session_dir = _write_run(tmp_path, "agy")

    assert delta == {}
    assert not (session_dir / EVENT_LOG_JSONL).exists()

    # POSITIVE CONTROL: the identical run under a surface that HAS a reader
    control_delta, control_dir = _write_run(tmp_path, "claude", name="control")
    assert control_delta and (control_dir / EVENT_LOG_JSONL).exists()


def test_no_op_when_no_transcript_was_resolved(tmp_path):
    """No resolver → nothing to read, and the step says so by writing nothing."""
    session_dir = _session(tmp_path, "claude")

    delta = _run(session_dir, tmp_path, None)

    assert delta == {}
    assert not (session_dir / EVENT_LOG_JSONL).exists()

    # POSITIVE CONTROL: the same session writes the log once a resolver is threaded
    control_delta, control_dir = _write_run(tmp_path, "claude", name="control")
    assert control_delta and (control_dir / EVENT_LOG_JSONL).exists()


# ---------------------------------------------------------------------------
# Fail-soft: a write that blows up never reaches the pipeline
# ---------------------------------------------------------------------------


def test_a_failed_write_does_not_fail_the_session(tmp_path):
    """A real failing write (the artifact path is a directory), swallowed.

    Positive control: the identical call writes the artifact when the path is
    free (asserted below), so the empty delta is the exception being caught —
    not a step that had nothing to write.
    """
    session_dir = _session(tmp_path, "claude")
    (session_dir / EVENT_LOG_JSONL).mkdir()  # open(…, "w") on it raises

    delta = _run(session_dir, tmp_path, _resolver_for(_transcript(tmp_path)))

    assert delta == {}, "a failed write must not produce an artifact key"
    assert (session_dir / EVENT_LOG_JSONL).is_dir(), "the blocker is still what it was"

    # POSITIVE CONTROL: with the path free, the same run writes the file
    control_delta, control_dir = _write_run(tmp_path, "claude", name="control")
    assert control_delta and (control_dir / EVENT_LOG_JSONL).is_file()


@pytest.mark.parametrize("broken", ["", "{not json", json.dumps({"provider": "no-such-surface"})])
def test_an_unreadable_metrics_json_is_a_no_op_not_a_failure(tmp_path, broken):
    """The provider is read back from metrics.json; every way that can fail no-ops."""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir(parents=True)
    (session_dir / METRICS_JSON).write_text(broken)

    delta = _run(session_dir, tmp_path, _resolver_for(_transcript(tmp_path)))

    assert delta == {}
    assert not (session_dir / EVENT_LOG_JSONL).exists()

    # POSITIVE CONTROL: the same transcript and dir, with a provider that resolves
    control_delta, control_dir = _write_run(tmp_path, "claude", name="control")
    assert control_delta and (control_dir / EVENT_LOG_JSONL).exists()
