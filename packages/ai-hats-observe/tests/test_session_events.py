"""The structured session artifact, and the projections read off it (HATS-1966 S5).

``audit.md`` is a rendering; this file is the record. So the artifact must
survive the round trip exactly, must tolerate being read while it is still being
written (HATS-1967 emits it live), and must be readable through a projection —
the same session shown to a judge without its reasoning and to an A/B comparison
with it.
"""

from __future__ import annotations

import stat
import threading
from pathlib import Path

import pytest

from dataclasses import replace

from ai_hats_observe.canonical import (
    ANSWER_ONLY,
    WITH_REASONING,
    AskKind,
    Blocking,
    GateDecision,
    GatePoint,
    GateVerdict,
    ItemDelta,
    ItemEmitted,
    ItemKind,
    Notice,
    PersonAsked,
    ResponseId,
    ResponseStarted,
    RunEnded,
    RunStarted,
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


def test_whose_work_an_event_is_survives_the_round_trip(
    tmp_path: Path, session_events: list
) -> None:
    """A sub-agent's events carry its id on every kind — the transcript's and
    the writer's and the chain's alike — so a reader never counts a child's
    call as the main agent's. Absent for the main agent, present for a child,
    and the file says which."""
    every_kind = [
        RunStarted(ts="2026-09-12T10:00:00.000Z"),
        *session_events,
        ItemDelta(response_id=ResponseId("r"), index=0, text="t"),
        PersonAsked(kind=AskKind.QUESTION, call_id="c9", tool="AskUserQuestion"),
        *VERDICTS,
        Notice(raw_code="x", source="test"),
        RunEnded(ok=True, raw_code="0"),
    ]
    tagged = [replace(e, agent="a25b9c51717cdb6ba") for e in every_kind]
    path = tmp_path / EVENT_LOG_JSONL

    write_events([*every_kind, *tagged], path)

    read = list(read_events(path))
    assert read == [*every_kind, *tagged]
    assert {e.agent for e in read[: len(every_kind)]} == {None}
    assert {e.agent for e in read[len(every_kind) :]} == {"a25b9c51717cdb6ba"}
    # the wire form spells the field only when there is one to spell
    lines = path.read_text(encoding="utf-8").splitlines()
    assert not any('"agent"' in line for line in lines[: len(every_kind)])
    assert all('"agent": "a25b9c51717cdb6ba"' in line for line in lines[len(every_kind) :])


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


def test_two_writers_appending_at_once_never_tear_a_line(tmp_path: Path) -> None:
    """Two producers append to this file — the session's writer and a hook
    process judging a call — and neither can see the other. Each line is one
    ``write(2)`` under ``O_APPEND``, which is what keeps them from interleaving;
    a buffered handle splits a large event across several and merges lines.

    The 40 KB results are the positive control: larger than any stdio buffer,
    so a buffered writer racing another tears lines here every time.
    """
    path = tmp_path / EVENT_LOG_JSONL
    per_writer, size = 100, 40_000

    def produce(tag: str) -> None:
        for index in range(per_writer):
            event = ToolResultReceived(call_id=f"{tag}-{index}", ok=True, content=tag * size)
            write_events([event], path, append=True)

    writers = [threading.Thread(target=produce, args=(tag,)) for tag in ("a", "b")]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) - 1 == 2 * per_writer, "a line was torn or merged"
    events = list(read_events(path))
    assert len(events) == 2 * per_writer
    assert {e.call_id for e in events} == {f"{t}-{i}" for t in "ab" for i in range(per_writer)}
    assert all(e.content == e.call_id[0] * size for e in events), "a payload was interleaved"


def test_the_artifact_is_private_from_creation(tmp_path: Path, session_events: list) -> None:
    path = tmp_path / EVENT_LOG_JSONL

    write_events(session_events, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
