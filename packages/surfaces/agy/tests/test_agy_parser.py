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
