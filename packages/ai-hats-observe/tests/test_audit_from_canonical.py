"""HATS-1966 S2 — the audit path reads the canonical events.

``ClaudeParser`` no longer walks the JSONL itself: it reads the transcript as
canonical events and derives the legacy shape from them. These tests are about
what that changed for a reader of ``audit.md`` and ``metrics.json`` — a call is
billed once, no fragment's prose is dropped, a tool's outcome is on its line,
and a run the platform killed says so.

Each names its positive control, because every defect here has a degenerate fix
that passes a naive assertion: suppress every repeated record and the token
count stops inflating; drop every successful tool result and only failures show.
"""  # comment-length: allow — why each test carries a control

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_hats_observe.audit import AuditWriter
from ai_hats_observe.canonical import Blocking, PersonMustAct
from ai_hats_observe.parsers.base import ParsedTranscript
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.session import Session

USAGE = {
    "input_tokens": 100,
    "output_tokens": 40,
    "cache_read_input_tokens": 7,
    "cache_creation_input_tokens": 3,
}


def fragment(
    request_id: str,
    content: list[dict[str, Any]],
    *,
    usage: dict[str, int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One record of one call: every fragment repeats the call's whole usage."""
    return {
        "type": "assistant",
        "requestId": request_id,
        "uuid": f"{request_id}-{len(content)}-{len(extra)}",
        "timestamp": "2026-09-12T10:00:02.000Z",
        "message": {
            "id": f"msg_{request_id}",
            "model": "claude-test-1",
            "stop_reason": "tool_use",
            "usage": dict(USAGE if usage is None else usage),
            "content": content,
        },
        **extra,
    }


def prompt(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": f"u-{text}",
        "timestamp": "2026-09-12T10:00:01.000Z",
        "message": {"role": "user", "content": text},
    }


def result(call_id: str, content: str, *, error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": f"r-{call_id}",
        "timestamp": "2026-09-12T10:00:03.000Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content,
                    "is_error": error,
                }
            ],
        },
    }


def parse(tmp_path: Path, records: list[dict[str, Any]]) -> ParsedTranscript:
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return ClaudeParser().parse(path, tmp_path / "absent.log")


def audit_of(tmp_path: Path, records: list[dict[str, Any]]) -> str:
    session_dir = tmp_path / "session_20260912-100000-1"
    session_dir.mkdir()
    session = Session(session_id="20260912-100000-1", session_dir=session_dir)
    session.init_audit(role="assistant", provider="claude")
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    AuditWriter().build(session, jsonl_path=path)
    return session.audit_path.read_text()


# --- cost ------------------------------------------------------------------


def test_a_call_split_across_fragments_is_billed_once(tmp_path: Path) -> None:
    """The 2.61x defect: a record is a fragment, and every fragment of a call
    repeats that call's usage byte-for-byte.

    Positive control: ``req-2`` is a single-record call whose usage must still
    arrive in full, so the count cannot be passing by suppressing repeats.
    """
    parsed = parse(
        tmp_path,
        [
            prompt("do it"),
            fragment("req-1", [{"type": "thinking", "thinking": "hm"}]),
            fragment("req-1", [{"type": "text", "text": "first"}]),
            fragment("req-1", [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {}}]),
            result("c1", "ok"),
            fragment("req-2", [{"type": "text", "text": "second"}], usage={"output_tokens": 11}),
        ],
    )

    assert parsed.agg_usage == {
        "input_tokens": 100,
        "output_tokens": 40 + 11,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 3,
    }
    # `calls` counts API calls, not the records the surface split them into.
    assert parsed.model_stats["claude-test-1"]["calls"] == 2
    assert [r.response_id for r in parsed.responses] == ["req-1", "req-2"]


# --- content ---------------------------------------------------------------


def test_every_fragment_of_an_answer_reaches_the_turn(tmp_path: Path) -> None:
    """45.3% of turns used to keep only the last text-bearing fragment.

    Positive control: the last fragment's text is asserted too — accumulating
    from the front instead of the back would be the same bug mirrored.
    """
    parsed = parse(
        tmp_path,
        [
            prompt("explain"),
            fragment("req-1", [{"type": "text", "text": "part one"}]),
            fragment("req-1", [{"type": "text", "text": "part two"}]),
        ],
    )

    assert "part one" in parsed.turns[0].response
    assert "part two" in parsed.turns[0].response


def test_reasoning_is_kept_as_text_not_as_a_fabricated_duration(tmp_path: Path) -> None:
    """``thinking_secs = len(text) // 200`` was a character count printed as
    seconds. The text is the thing; no surface reported a duration here."""
    parsed = parse(
        tmp_path,
        [
            prompt("think"),
            fragment("req-1", [{"type": "thinking", "thinking": "weighing the options"}]),
            fragment("req-1", [{"type": "text", "text": "done"}]),
        ],
    )

    assert parsed.turns[0].thinking == ["weighing the options"]
    assert parsed.turns[0].thinking_secs == 0
    assert "💭 weighing the options" in audit_of(
        tmp_path,
        [
            prompt("think"),
            fragment("req-1", [{"type": "thinking", "thinking": "weighing the options"}]),
        ],
    )


def test_a_tool_failure_reaches_the_audit_with_its_message(tmp_path: Path) -> None:
    """3458 failed tool results in the corpus reached no artifact at all.

    Positive control: the successful call is rendered on the same audit, so the
    assertion cannot be satisfied by a writer that drops every success.
    """
    audit = audit_of(
        tmp_path,
        [
            prompt("build it"),
            fragment(
                "req-1",
                [
                    {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "ls"}},
                    {
                        "type": "tool_use",
                        "id": "c2",
                        "name": "Bash",
                        "input": {"command": "badcmd"},
                    },
                ],
            ),
            result("c1", "file1"),
            result("c2", "command not found: badcmd", error=True),
        ],
    )

    assert "🔧 Bash: ls ✓" in audit
    assert "🔧 Bash: badcmd ✗ command not found: badcmd" in audit


# --- run health ------------------------------------------------------------


def test_a_run_the_platform_killed_says_so(tmp_path: Path) -> None:
    """15 sessions in the corpus ended on a spend-limit notice, and every
    artifact we produced showed a run that merely stopped talking.

    Positive control: the notice's own prose must still be *somewhere* — as the
    signal's detail — so an empty audit would not pass this either.
    """
    records = [
        prompt("keep going"),
        fragment("req-1", [{"type": "text", "text": "working on it"}]),
        {
            **fragment("req-2", [{"type": "text", "text": "You've hit your monthly spend limit"}]),
            "isApiErrorMessage": True,
            "error": "billing_error",
            "apiErrorStatus": 403,
        },
    ]
    parsed = parse(tmp_path, records)
    audit = audit_of(tmp_path, records)

    blocking = [s for s in parsed.signals if isinstance(s, Blocking)]
    assert [s.reason for s in blocking] == [PersonMustAct.PAY]
    assert "## Signals" in audit
    assert "a person must pay" in audit
    assert "You've hit your monthly spend limit" in audit
    # The contamination rule: that prose is the signal's, never the answer's.
    assert "👾 You've hit your monthly spend limit" not in audit
    assert parsed.turns[0].response == "working on it"


def test_parse_quality_and_run_health_stay_separate_axes(tmp_path: Path) -> None:
    """``flags`` gates trace deletion and ``measured: false``; a blocked run is
    still a perfectly measured one, so it must not land there."""
    parsed = parse(
        tmp_path,
        [
            prompt("go"),
            fragment("req-1", [{"type": "text", "text": "ok"}]),
            {"type": "not-a-real-type", "timestamp": "2026-09-12T10:00:04.000Z"},
        ],
    )

    assert parsed.flags == []
    assert [s.raw_code for s in parsed.signals] == ["not-a-real-type"]


# --- the surfaces that have not opted in -----------------------------------


def test_a_surface_that_reports_no_events_keeps_working(tmp_path: Path) -> None:
    """``responses``/``signals`` default empty, so agy, cline and the trace
    fallback compile and behave exactly as before."""
    trace = tmp_path / "trace.log"
    trace.write_text("18:15:00.000 [REQ] find the file\n18:15:01.000 [RES] ⏺Found it\n")

    for parsed in (ParsedTranscript(turns=[]), TraceParser().parse(None, trace)):
        assert parsed.responses == []
        assert parsed.signals == []


def test_every_reasoning_block_of_a_turn_is_kept(tmp_path: Path) -> None:
    """Reasoning accumulates for the same reason prose does: a turn spans several
    fragments, and keeping only the last drops the rest.

    The sibling test covers that for the answer. This covers it for reasoning,
    where a single-block fixture passes either way — which is how the gap got in.

    Positive control: order is asserted, so collecting into a set would not pass.
    """
    parsed = parse(
        tmp_path,
        [
            prompt("go"),
            fragment("req-1", [{"type": "thinking", "thinking": "first consideration"}]),
            fragment("req-1", [{"type": "thinking", "thinking": "second consideration"}]),
        ],
    )

    assert parsed.turns[0].thinking == ["first consideration", "second consideration"]


def test_an_audit_built_from_the_event_log_is_the_audit_built_from_the_transcript(
    tmp_path: Path,
) -> None:
    """The event log is a record of the session, not a lossy copy of one.

    An audit rendered from the log must equal the audit rendered from the
    transcript that produced it — otherwise the log is a summary, and every
    consumer built on it later inherits whatever it silently dropped.

    Positive control: the fixture is asserted to carry turns, reasoning, a tool
    outcome and a signal, so equality cannot be reached by both sides being
    empty.
    """
    from ai_hats_observe.event_log import read_events, write_events
    from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

    records = [
        prompt("do the thing"),
        fragment("req-1", [{"type": "thinking", "thinking": "weighing it"}]),
        fragment("req-1", [{"type": "text", "text": "starting"}]),
        fragment(
            "req-1",
            [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "ls"}}],
        ),
        result("c1", "no such file", error=True),
        fragment("req-1", [{"type": "text", "text": "it failed"}], stop_reason="end_turn"),
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    from_transcript = ClaudeParser().parse(transcript, tmp_path / "absent.log")

    log = tmp_path / "events.jsonl"
    write_events(ClaudeTranscriptReader(transcript).read(), log)
    from_log = ClaudeParser.from_events(read_events(log))

    # POSITIVE CONTROL: the fixture is rich enough for equality to mean something
    assert from_transcript.turns, "fixture produced no turns"
    assert from_transcript.turns[0].thinking == ["weighing it"]
    assert any("✗" in tool for tool in from_transcript.turns[0].tools)
    assert from_transcript.agg_usage["output_tokens"] > 0

    assert from_log.turns == from_transcript.turns
    assert from_log.agg_usage == from_transcript.agg_usage
    assert from_log.model_stats == from_transcript.model_stats
    assert from_log.signals == from_transcript.signals
    assert from_log.responses == from_transcript.responses
