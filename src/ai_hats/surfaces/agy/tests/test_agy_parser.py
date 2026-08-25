"""Unit tests for AgyParser and AgyProvider.resolve_transcript (HATS-1391)."""

from __future__ import annotations

import json
from pathlib import Path
from ai_hats.surfaces.agy.parser import AgyParser
from ai_hats_observe.artifacts import (
    FLAG_NO_TOKEN_TELEMETRY,
    FLAG_TOKEN_TELEMETRY_ESTIMATED,
)
from ai_hats.surfaces.agy.provider import AgyProvider


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
    jsonl_path.write_text("\n".join(json.dumps(line) for line in lines))
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
    # HATS-1433: counts exist here (estimated off turn text), so the record says
    # "estimated" rather than "unavailable" — the number is not absent, just unmeasured.
    assert "token-telemetry-estimated" in usage["flags"]
    assert usage["aggregates"]["input_tokens"] > 0 or usage["aggregates"]["output_tokens"] > 0


def test_agy_provider_resolve_transcript(tmp_path: Path, monkeypatch) -> None:
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(gemini_home))

    provider = AgyProvider()
    session_id = "20260730-120000-1-12345"

    # Absent directory -> empty list
    assert provider.resolve_transcript(tmp_path, session_id) == []

    # Create brain transcript 1
    log_dir1 = (
        gemini_home / "antigravity-cli" / "brain" / "conv-uuid-123" / ".system_generated" / "logs"
    )
    log_dir1.mkdir(parents=True)
    transcript_file1 = log_dir1 / "transcript.jsonl"
    transcript_file1.write_text("{}")

    # Resolved
    resolved = provider.resolve_transcript(tmp_path, session_id)
    assert resolved == [transcript_file1]

    # Exact path via provider_session_id
    exact = provider.resolve_transcript(tmp_path, session_id, provider_session_id="conv-uuid-123")
    assert exact == [transcript_file1]


def test_agy_provider_resolve_transcript_multiple_segments(tmp_path: Path, monkeypatch) -> None:
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(gemini_home))

    provider = AgyProvider()
    session_id = "20260730-120000-1-12345"

    log_dir1 = (
        gemini_home / "antigravity-cli" / "brain" / "conv-uuid-1" / ".system_generated" / "logs"
    )
    log_dir1.mkdir(parents=True)
    t1 = log_dir1 / "transcript.jsonl"
    t1.write_text('{"step_index":0}')

    log_dir2 = (
        gemini_home / "antigravity-cli" / "brain" / "conv-uuid-2" / ".system_generated" / "logs"
    )
    log_dir2.mkdir(parents=True)
    t2 = log_dir2 / "transcript.jsonl"
    t2.write_text('{"step_index":1}')

    resolved = provider.resolve_transcript(tmp_path, session_id)
    assert len(resolved) == 2
    assert set(resolved) == {t1, t2}


def test_agy_parser_merges_multiple_jsonl_paths(tmp_path: Path) -> None:
    seg1 = tmp_path / "seg1.jsonl"
    seg1.write_text(
        json.dumps(
            {
                "type": "USER_INPUT",
                "content": "first question",
                "created_at": "2026-07-31T10:00:00Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "content": "first answer",
                "created_at": "2026-07-31T10:00:05Z",
                "tool_calls": [{"name": "grep_search", "args": {"Query": "test"}}],
            }
        )
        + "\n"
    )
    seg2 = tmp_path / "seg2.jsonl"
    seg2.write_text(
        json.dumps(
            {
                "type": "USER_INPUT",
                "content": "second question",
                "created_at": "2026-07-31T10:05:00Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "content": "second answer",
                "created_at": "2026-07-31T10:05:05Z",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}],
            }
        )
        + "\n"
    )

    trace = tmp_path / "trace.log"
    trace.write_text("")

    parser = AgyParser()
    parsed = parser.parse([seg1, seg2], trace)

    assert len(parsed.turns) == 2
    assert parsed.turns[0].user_input == "first question"
    assert parsed.turns[1].user_input == "second question"
    assert "grep_search: test" in parsed.turns[0].tools[0]
    assert "run_command: pytest" in parsed.turns[1].tools[0]

    usage = parser.parse_usage([seg1, seg2], trace)
    assert usage["aggregates"]["tool_calls"] == 2


def test_the_richer_source_wins_when_the_transcript_is_a_tail_fragment(tmp_path):
    """agy rotates its brain segment on a checkpoint (HATS-1397).

    Measured on a live HITL run: the resolved transcript held 4 records of a
    42-record conversation, while trace.log held all 7 user turns. With no
    provider session id there is no way to resolve the earlier segments, so the
    parse takes whichever source actually carries the session.
    """
    fragment = tmp_path / "transcript.jsonl"
    fragment.write_text(
        json.dumps(
            {"type": "USER_INPUT", "content": "last question", "created_at": "2026-07-31T10:04:00"}
        )
        + "\n"
        + json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "content": "the tail answer",
                "created_at": "2026-07-31T10:04:05",
            }
        )
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
    assert "token-telemetry-estimated" in parsed.flags
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
                {
                    "type": "PLANNER_RESPONSE",
                    "content": "first",
                    "created_at": "2026-07-31T10:00:01",
                },
                {"type": "USER_INPUT", "content": "two", "created_at": "2026-07-31T10:01:00"},
                {
                    "type": "PLANNER_RESPONSE",
                    "content": "second",
                    "created_at": "2026-07-31T10:01:01",
                },
            )
        )
    )
    trace = tmp_path / "trace.log"
    trace.write_text("13:04:00.000 [REQ] two\n13:04:05.000 [RES] ⏺ second\n")

    parsed = AgyParser().parse(full, trace)

    assert [t.user_input for t in parsed.turns] == ["one", "two"]


def test_agy_parser_extracts_tokens_from_metrics_json(tmp_path: Path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text("13:00:00.000 [REQ] test\n13:00:01.000 [RES] ⏺ ok\n")

    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "input": 150,
                    "output": 50,
                    "cache_read": 1000,
                    "cache_creation": 200,
                }
            }
        )
    )

    parser = AgyParser()
    parsed = parser.parse(None, trace)
    assert parsed.agg_usage["input_tokens"] == 150
    assert parsed.agg_usage["output_tokens"] == 50
    assert parsed.agg_usage["cache_read_input_tokens"] == 1000
    assert parsed.agg_usage["cache_creation_input_tokens"] == 200
    assert "token-telemetry-unavailable" not in parsed.flags

    usage = parser.parse_usage(None, trace)
    assert usage["aggregates"]["input_tokens"] == 150
    assert usage["aggregates"]["output_tokens"] == 50
    assert "token-telemetry-unavailable" not in usage["flags"]


def test_agy_parser_extracts_tokens_from_trace_log(tmp_path: Path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text(
        "13:00:00.000 [RES] ▸ Thought for 2s, 495 tokens\n"
        "13:00:05.000 [RES] (1m 10s · ↓ 4.2k tokens)\n"
    )
    parser = AgyParser()
    parsed = parser.parse(None, trace)
    assert parsed.agg_usage["output_tokens"] == 495
    assert parsed.agg_usage["input_tokens"] == 4200
    assert "token-telemetry-estimated" in parsed.flags


def test_agy_parser_does_not_drop_same_second_tool_calls_with_empty_content(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "USER_INPUT", "content": "do tasks", "created_at": "2026-07-31T10:00:00Z"},
        {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "content": "",
            "created_at": "2026-07-31T10:00:01Z",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "cmd1"}}],
        },
        {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "content": "",
            "created_at": "2026-07-31T10:00:01Z",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "cmd2"}}],
        },
    ]
    jsonl_path.write_text("\n".join(json.dumps(rec) for rec in lines) + "\n")
    trace = tmp_path / "trace.log"
    trace.write_text("")

    parser = AgyParser()
    parsed = parser.parse(jsonl_path, trace)
    assert len(parsed.turns[0].tools) == 2
    assert "cmd1" in parsed.turns[0].tools[0]
    assert "cmd2" in parsed.turns[0].tools[1]


def test_agy_parser_preserves_trace_flag_when_trace_wins(tmp_path: Path) -> None:
    fragment = tmp_path / "transcript.jsonl"
    fragment.write_text(
        json.dumps({"type": "USER_INPUT", "content": "q3", "created_at": "2026-07-31T10:04:00Z"})
        + "\n"
    )
    trace = tmp_path / "trace.log"
    trace.write_text("10:00:00.000 [REQ] q1\n10:01:00.000 [REQ] q2\n10:04:00.000 [REQ] q3\n")

    parser = AgyParser()
    parsed = parser.parse(fragment, trace)
    assert "trace-used" in parsed.flags


def test_agy_parser_filters_records_before_session_start(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "transcript.jsonl"
    records = [
        {"type": "USER_INPUT", "content": "old question", "created_at": "2026-07-31T09:00:00Z"},
        {"type": "USER_INPUT", "content": "new question", "created_at": "2026-07-31T10:00:00Z"},
    ]
    jsonl_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    parser = AgyParser()
    lines = parser._load_lines(jsonl_path, session_start_iso="2026-07-31T09:30:00Z")
    assert lines is not None
    assert len(lines) == 1
    assert lines[0]["content"] == "new question"


# ---------------------------------------------------------------------------
# Token provenance: measured vs estimated (HATS-1433)
# ---------------------------------------------------------------------------


def _jsonl_one_turn(tmp_path: Path) -> Path:
    jsonl_path = tmp_path / "transcript.jsonl"
    jsonl_path.write_text(
        "\n".join(
            json.dumps(line)
            for line in (
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "created_at": "2026-08-01T12:00:00Z",
                    "content": "<USER_REQUEST>\nsome question\n</USER_REQUEST>",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-08-01T12:00:01Z",
                    "content": "some answer",
                },
            )
        )
        + "\n"
    )
    return jsonl_path


def test_tokens_from_metrics_are_not_flagged_as_estimated(tmp_path: Path) -> None:
    # GIVEN a session whose metrics.json carries a real token count
    jsonl_path = _jsonl_one_turn(tmp_path)
    trace = tmp_path / "trace.log"
    trace.write_text("12:00:00.000 [REQ] some question\n")
    (tmp_path / "metrics.json").write_text(
        json.dumps({"tokens": {"input": 150, "output": 50, "cache_read": 0, "cache_creation": 0}})
    )

    parsed = AgyParser().parse(jsonl_path, trace)

    # THEN it is a measurement and carries no telemetry flag at all
    assert parsed.agg_usage["input_tokens"] == 150
    assert parsed.flags == []


def test_tokens_scraped_from_the_trace_text_are_flagged_as_estimated(tmp_path: Path) -> None:
    # GIVEN no metrics.json, but token counts rendered in the TUI trace
    jsonl_path = _jsonl_one_turn(tmp_path)
    trace = tmp_path / "trace.log"
    trace.write_text(
        "13:00:00.000 [RES] ▸ Thought for 2s, 495 tokens\n"
        "13:00:05.000 [RES] (1m 10s · ↓ 4.2k tokens)\n"
    )

    parsed = AgyParser().parse(jsonl_path, trace)
    usage = AgyParser().parse_usage(jsonl_path, trace)

    # THEN the numbers are kept, but nobody may read them as measured
    assert parsed.agg_usage["output_tokens"] > 0
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in parsed.flags
    assert FLAG_NO_TOKEN_TELEMETRY not in parsed.flags
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in usage["flags"]


def test_tokens_estimated_from_turn_text_are_flagged_as_estimated(tmp_path: Path) -> None:
    # GIVEN neither metrics.json nor any token count in the trace — only turn text
    jsonl_path = _jsonl_one_turn(tmp_path)
    trace = tmp_path / "trace.log"
    trace.write_text("14:00:00.000 [REQ] some question\n")

    parsed = AgyParser().parse(jsonl_path, trace)

    # THEN the count is a guess off string lengths and says so
    assert parsed.agg_usage["output_tokens"] > 0
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in parsed.flags


def test_no_source_at_all_still_reports_unavailable(tmp_path: Path) -> None:
    # GIVEN a transcript with no text to measure or estimate from
    jsonl_path = tmp_path / "transcript.jsonl"
    jsonl_path.write_text(
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-08-01T12:00:00Z",
                "content": "<USER_REQUEST>\n\n</USER_REQUEST>",
            }
        )
        + "\n"
    )
    trace = tmp_path / "trace.log"
    trace.write_text("")

    parsed = AgyParser().parse(jsonl_path, trace)

    # THEN the pre-HATS-1427 contract stands: zeros, and the flag that says why
    assert parsed.agg_usage["output_tokens"] == 0
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED not in parsed.flags


def test_a_half_scraped_trace_does_not_invent_the_other_half(tmp_path: Path) -> None:
    # GIVEN a trace that renders an output count and nothing about input, and no
    # turn text to estimate the missing half from
    trace = tmp_path / "trace.log"
    trace.write_text("13:00:00.000 [RES] ▸ Thought for 2s, 495 tokens\n")

    parsed = AgyParser().parse(None, trace)

    # THEN the unknown half stays 0 instead of the invented constant it used to get
    assert parsed.agg_usage["output_tokens"] == 495
    assert parsed.agg_usage["input_tokens"] == 0
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in parsed.flags
