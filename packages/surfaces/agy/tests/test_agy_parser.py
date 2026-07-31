"""Unit tests for AgyParser and AgyProvider.resolve_transcript (HATS-1391)."""

from __future__ import annotations

import json
from pathlib import Path
from ai_hats_agy.parser import AgyParser
from ai_hats_agy.provider import AgyProvider


def test_agy_parser_fallback_when_jsonl_absent(tmp_path: Path) -> None:
    parser = AgyParser()
    trace_path = tmp_path / "trace.log"
    trace_path.write_text("12:00:00.000 [REQ] hello\n12:00:01.000 [RES] ⏺ hi\n")

    parsed = parser.parse(None, trace_path)
    assert parsed.turns  # trace fallback produces turns
    usage = parser.parse_usage(None, trace_path)
    assert usage["schema_version"] == "usage/v1"


def test_agy_parser_parses_transcript_jsonl(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "transcript.jsonl"
    lines = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-07-30T12:00:00Z",
            "content": "<USER_REQUEST>\nFix the bug in main.py\n</USER_REQUEST>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-07-30T12:00:05Z",
            "thinking": "Let me read the file first and run a test.",
            "tool_calls": [
                {"name": "view_file", "args": {"AbsolutePath": "/app/main.py"}},
                {"name": "run_command", "args": {"CommandLine": "pytest"}},
            ],
            "content": "I am inspecting main.py and running tests.",
        },
    ]
    jsonl_path.write_text("\n".join(json.dumps(l) for l in lines))
    trace_path = tmp_path / "trace.log"

    parser = AgyParser()
    parsed = parser.parse(jsonl_path, trace_path)

    assert len(parsed.turns) == 1
    turn = parsed.turns[0]
    assert turn.user_input == "Fix the bug in main.py"
    assert turn.thinking_secs >= 1
    assert len(turn.tools) == 2
    assert "view_file: /app/main.py" in turn.tools[0]
    assert "run_command: pytest" in turn.tools[1]
    assert turn.response == "I am inspecting main.py and running tests."

    usage = parser.parse_usage(jsonl_path, trace_path)
    assert usage["aggregates"]["tool_calls"] == 2
    assert any("token-telemetry-unavailable" in f for f in usage["flags"])


def test_agy_provider_resolve_transcript(tmp_path: Path, monkeypatch) -> None:
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(gemini_home))

    provider = AgyProvider()
    session_id = "20260730-120000-1-12345"

    # Absent directory -> None
    assert provider.resolve_transcript(tmp_path, session_id) is None

    # Create brain transcript
    log_dir = gemini_home / "antigravity-cli" / "brain" / "conv-uuid-123" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    transcript_file = log_dir / "transcript.jsonl"
    transcript_file.write_text("{}")

    # Resolved
    resolved = provider.resolve_transcript(tmp_path, session_id)
    assert resolved == transcript_file

    # Exact path via provider_session_id
    exact = provider.resolve_transcript(tmp_path, session_id, provider_session_id="conv-uuid-123")
    assert exact == transcript_file


def test_the_richer_source_wins_when_the_transcript_is_a_tail_fragment(tmp_path):
    """agy rotates its brain segment on a checkpoint (HATS-1397).

    Measured on a live HITL run: the resolved transcript held 4 records of a
    42-record conversation, while trace.log held all 7 user turns. With no
    provider session id there is no way to resolve the earlier segments, so the
    parse takes whichever source actually carries the session.
    """
    fragment = tmp_path / "transcript.jsonl"
    fragment.write_text(
        json.dumps({"type": "USER_INPUT", "content": "last question", "created_at": "2026-07-31T10:04:00"})
        + "\n"
        + json.dumps({"type": "PLANNER_RESPONSE", "content": "the tail answer", "created_at": "2026-07-31T10:04:05"})
        + "\n"
    )
    trace = tmp_path / "trace.log"
    trace.write_text(
        "12:50:00.000 [REQ] first question\n"
        "12:50:01.000 [RES] ⏺ Bash(rack ls)\n"
        "12:50:02.000 [RES] ⏺ here is the backlog\n"
        "12:55:00.000 [REQ] second question\n"
        "12:55:01.000 [RES] ⏺ and here is the answer\n"
        "13:04:00.000 [REQ] last question\n"
        "13:04:05.000 [RES] ⏺ the tail answer\n"
    )

    parsed = AgyParser().parse(fragment, trace)

    assert len(parsed.turns) == 3, "the tail fragment displaced the whole conversation"
    assert parsed.turns[0].user_input == "first question"
    assert "token-telemetry-unavailable" in parsed.flags
    assert "no-structured-transcript" not in parsed.flags, (
        "a structured transcript did exist — marking the record unmeasured would "
        "drop its counters and make auto_retro skip the session"
    )


def test_the_structured_transcript_wins_when_it_covers_the_session(tmp_path):
    """The trace scrape is heuristic — it must not displace a complete transcript."""
    full = tmp_path / "transcript.jsonl"
    full.write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in (
                {"type": "USER_INPUT", "content": "one", "created_at": "2026-07-31T10:00:00"},
                {"type": "PLANNER_RESPONSE", "content": "first", "created_at": "2026-07-31T10:00:01"},
                {"type": "USER_INPUT", "content": "two", "created_at": "2026-07-31T10:01:00"},
                {"type": "PLANNER_RESPONSE", "content": "second", "created_at": "2026-07-31T10:01:01"},
            )
        )
    )
    trace = tmp_path / "trace.log"
    trace.write_text("13:04:00.000 [REQ] two\n13:04:05.000 [RES] ⏺ second\n")

    parsed = AgyParser().parse(full, trace)

    assert [t.user_input for t in parsed.turns] == ["one", "two"]
