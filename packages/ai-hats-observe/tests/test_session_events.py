"""The structured session artifact, and the projections read off it (HATS-1966 S5).

``audit.md`` is a rendering; this file is the record. So the artifact must
survive the round trip exactly, must tolerate being read while it is still being
written (HATS-1967 emits it live), and must be readable through a projection —
the same session shown to a judge without its reasoning and to an A/B comparison
with it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_hats_observe import event_log
from ai_hats_observe.canonical import (
    ANSWER_ONLY,
    WITH_REASONING,
    Blocking,
    GateDecision,
    GatePoint,
    GateVerdict,
    ItemEmitted,
    ItemKind,
    ResponseStarted,
    ToolResultReceived,
    collect,
    select,
)
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events, write_events
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"


def _events(name: str) -> list:
    return list(ClaudeTranscriptReader(TRANSCRIPTS / name).read())


@pytest.fixture
def session_events() -> list:
    """One call spread over three records (thinking, text, tool_use), its tool
    result, and a second call — every event kind this surface produces."""
    return _events("fragments.jsonl")


# --- the artifact ----------------------------------------------------------


@pytest.mark.parametrize("fixture", ["fragments.jsonl", "api_error.jsonl"])
def test_the_artifact_round_trips(tmp_path: Path, fixture: str) -> None:
    """Written then read back, event for event — including the signal axis,
    which ``api_error.jsonl`` carries and ``fragments.jsonl`` does not."""
    events = _events(fixture)
    path = tmp_path / EVENT_LOG_JSONL

    written = write_events(events, path)

    assert written == len(events)
    assert list(read_events(path)) == events
    assert len(events) > 3, "fixture is too thin to prove a round trip"


def test_a_torn_final_line_still_reads_every_event_before_it(
    tmp_path: Path, session_events: list
) -> None:
    """A reader may arrive mid-write. The half-written line is simply not there
    yet; everything already committed still reads, and nothing raises.

    Positive control: the intact file yields one MORE event than the torn one,
    so the test cannot pass on a reader that returns nothing — and the tear is
    proved to have landed inside a line, not on a boundary.
    """
    path = tmp_path / EVENT_LOG_JSONL
    write_events(session_events, path)
    intact = path.read_bytes()
    last_line_start = intact.rstrip(b"\n").rfind(b"\n") + 1
    torn = intact[: last_line_start + 20]
    assert not torn.endswith(b"\n"), "the tear must land inside a line"
    path.write_bytes(torn)

    read_back = list(read_events(path))

    assert read_back == session_events[:-1]
    assert len(read_back) == len(session_events) - 1


def test_a_live_writer_appends(tmp_path: Path, session_events: list) -> None:
    """The shape HATS-1967 needs: a run's events arrive in batches and the file
    grows, rather than being rewritten from the top each time."""
    path = tmp_path / EVENT_LOG_JSONL
    head, tail = session_events[:2], session_events[2:]

    write_events(head, path)
    assert list(read_events(path)) == head

    write_events(tail, path, append=True)
    assert list(read_events(path)) == session_events


def test_each_event_reaches_the_file_as_one_write(
    tmp_path: Path, session_events: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two producers append to this file — the session's writer and a hook
    process judging a call — and neither can see the other. One ``write(2)``
    per line is what keeps their lines from interleaving under ``O_APPEND``;
    a buffered handle splits a large event across several.

    The 40 KB tool result is the positive control: it is larger than any stdio
    buffer, so a writer that went through one would show up as several calls.
    """
    big = ToolResultReceived(call_id="c-big", ok=True, content="x" * 40_000)
    events = [*session_events, big]
    path = tmp_path / EVENT_LOG_JSONL
    sizes: list[int] = []
    real_write = os.write

    def counting_write(fd: int, data: bytes) -> int:
        sizes.append(len(data))
        return real_write(fd, data)

    monkeypatch.setattr(event_log.os, "write", counting_write)

    write_events(events, path)

    assert len(sizes) == len(events), f"one write per event expected, saw {len(sizes)}"
    assert max(sizes) > 40_000, "the large event must be a single write"
    assert list(read_events(path)) == events


def test_an_absent_artifact_reads_as_no_events(tmp_path: Path) -> None:
    assert list(read_events(tmp_path / "never-written.jsonl")) == []


# --- gate verdicts -----------------------------------------------------------


VERDICTS = [
    GateVerdict(
        point=GatePoint.BEFORE_TOOL,
        decision=GateDecision.DENY,
        hook="safety_gate.py",
        reason="rm -rf outside the worktree",
        nudges=(("budget_nudge.py", "prefer rack transition"),),
        tool="Bash",
        call_id="toolu_01",
        source="chain",
        ts="2026-09-14T12:00:00Z",
    ),
    GateVerdict(point=GatePoint.AFTER_TOOL, decision=GateDecision.ALLOW, source="chain"),
    GateVerdict(
        point=GatePoint.AT_STOP,
        decision=GateDecision.ASK,
        hook="claude-stop-hook.sh",
        source="claude/transcript",
        ts="2026-09-14T12:00:01Z",
    ),
]


def test_a_gate_verdict_round_trips(tmp_path: Path, session_events: list) -> None:
    """A verdict is an event like any other: written, read back equal, with
    every field a consumer attributes it by — the deciding hook, the call it
    judged, and who spoke."""
    path = tmp_path / EVENT_LOG_JSONL
    events = [*session_events[:2], *VERDICTS, *session_events[2:]]

    write_events(events, path)

    assert list(read_events(path)) == events


def test_a_gate_verdict_is_carried_by_a_projection_and_folded_by_none(
    tmp_path: Path, session_events: list
) -> None:
    """The log is the deliverable; the collected shape audit.md and usage.json
    are built from does not change because gates spoke. A projection still
    passes the verdict through — narrowing what a consumer reads must not hide
    that a call was refused."""
    events = [*session_events[:2], *VERDICTS, *session_events[2:]]

    assert collect(events) == collect(session_events)
    assert [e for e in select(events, ANSWER_ONLY) if isinstance(e, GateVerdict)] == VERDICTS


# --- projections -----------------------------------------------------------


def _items(events, kinds) -> list:
    return [e.item for e in select(events, kinds) if isinstance(e, ItemEmitted)]


def test_answer_only_withholds_the_reasoning(tmp_path: Path, session_events: list) -> None:
    """A judge scores what a run produced, not the route it took — so the
    reasoning is not in the view it reads.

    Positive control: the same session read ``WITH_REASONING`` DOES carry the
    thinking, so this assertion cannot be satisfied by a fixture that never had
    any, or by a projection that drops everything.
    """
    path = tmp_path / EVENT_LOG_JSONL
    write_events(session_events, path)

    judged = _items(read_events(path), ANSWER_ONLY)
    compared = _items(read_events(path), WITH_REASONING)

    assert [i.kind for i in judged if i.kind is ItemKind.THINKING] == []
    assert [i.kind for i in compared if i.kind is ItemKind.THINKING] != []
    # the answer itself survives both — the view narrows, it does not empty
    assert "task started" in "".join(i.text for i in judged if i.kind is ItemKind.TEXT)


def test_with_reasoning_keeps_the_route(tmp_path: Path, session_events: list) -> None:
    """An A/B comparison asks *why* one run beat the other, so its view carries
    the thinking, the tool call and the tool result.

    Positive control: the ``ANSWER_ONLY`` view of the same file has no thinking,
    so this is not a test that every view is identical.
    """
    path = tmp_path / EVENT_LOG_JSONL
    write_events(session_events, path)

    compared = list(select(read_events(path), WITH_REASONING))
    thinking = [i.text for i in _items(compared, WITH_REASONING) if i.kind is ItemKind.THINKING]

    assert thinking == ["the user wants the backlog skill"]
    assert any(isinstance(e, ToolResultReceived) for e in compared)
    assert _items(read_events(path), ANSWER_ONLY) != _items(read_events(path), WITH_REASONING)


def test_a_projection_never_hides_that_the_run_was_blocked(tmp_path: Path) -> None:
    """Narrowing what a consumer reads must not make a killed run look clean:
    the signal survives the view a judge reads."""
    path = tmp_path / EVENT_LOG_JSONL
    write_events(_events("api_error.jsonl"), path)

    judged = collect(select(read_events(path), ANSWER_ONLY))

    assert isinstance(judged.blocked_by, Blocking)
    assert judged.api_calls == 1
    assert any(isinstance(e, ResponseStarted) for e in select(read_events(path), ANSWER_ONLY))
