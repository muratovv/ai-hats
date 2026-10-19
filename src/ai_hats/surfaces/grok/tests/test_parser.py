"""GrokParser tests. The happy path is the S1 headless session, unmodified."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY

from ai_hats.surfaces.grok.parser import GrokParser


def _fixture() -> Path:
    return Path(
        str(files("ai_hats.surfaces.grok.tests").joinpath("fixtures/headless-pong/updates.jsonl"))
    )


def test_parse_headless_pong_uses_usage_json_not_the_turn_twice():
    parsed = GrokParser().parse(_fixture(), Path("/nonexistent.trace"))
    assert len(parsed.turns) == 1
    turn = parsed.turns[0]
    assert turn.user_input == "Reply with exactly: pong"
    assert turn.response == "pong"
    assert turn.timestamp
    # 15079 full prompt − 9600 cache read. Summing turn_completed on top would be 10958.
    assert parsed.agg_usage["input_tokens"] == 5479
    assert parsed.agg_usage["output_tokens"] == 30
    assert parsed.agg_usage["cache_read_input_tokens"] == 9600
    assert parsed.agg_usage["cache_creation_input_tokens"] == 0
    assert parsed.model_stats["grok-4.7-build"] == {"calls": 1, "in": 5479, "out": 30}
    assert FLAG_NO_TOKEN_TELEMETRY not in parsed.flags
    assert "schema-drift" not in parsed.flags


def test_parse_usage_matches_the_transcript_totals():
    report = GrokParser().parse_usage(_fixture(), Path("/nonexistent.trace"))
    assert report["schema_version"] == "usage/v2"
    assert report["session_id"] == "sess-grok-pong"
    assert report["api_calls"] == 1
    assert report["usage_totals"]["input_tokens"] == 5479
    assert report["usage_totals"]["output_tokens"] == 30
    assert report["usage_totals"]["cache_read_input_tokens"] == 9600
    assert report["entry_types_seen"]["hook_execution"] == 2
    assert "schema-drift" not in report["flags"]


def test_unknown_session_update_is_not_drift(tmp_path: Path):
    wire = tmp_path / "updates.jsonl"
    wire.write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {"update": {"sessionUpdate": "current_mode_update"}},
            }
        )
        + "\n"
    )
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" not in parsed.flags
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags


def test_tool_call_is_counted_once(tmp_path: Path):
    def line(update: dict) -> str:
        return json.dumps({"timestamp": 10, "params": {"update": update}})

    tool = {
        "sessionUpdate": "tool_call",
        "toolCallId": "call-1",
        "_meta": {"x.ai/tool": {"name": "read_file"}},
    }
    wire = tmp_path / "updates.jsonl"
    wire.write_text(
        "\n".join(
            [
                line(
                    {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "look"},
                    }
                ),
                line(
                    {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "checking"},
                    }
                ),
                line(tool),
                line({**tool, "sessionUpdate": "tool_call_update"}),
                line(
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "done"},
                    }
                ),
            ]
        )
        + "\n"
    )
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert parsed.turns[0].tools == ["read_file"]
    assert parsed.turns[0].thinking == ["checking"]
    assert parsed.turns[0].response == "done"
    report = GrokParser().parse_usage(wire, tmp_path / "t.trace")
    assert report["aggregates"]["tool_calls"] == 1


def test_turn_completed_is_the_ledger_when_usage_json_is_absent(tmp_path: Path):
    wire = tmp_path / "updates.jsonl"
    wire.write_text(
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "usage": {
                            "inputTokens": 100,
                            "cachedReadTokens": 40,
                            "cacheCreationTokens": 0,
                            "outputTokens": 4,
                            "modelCalls": 1,
                            "primaryModelId": "grok-test",
                        },
                    }
                },
            }
        )
        + "\n"
    )
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert parsed.agg_usage["input_tokens"] == 60
    assert parsed.agg_usage["output_tokens"] == 4
    assert parsed.model_stats["grok-test"]["calls"] == 1


def test_empty_transcript_flags_no_telemetry(tmp_path: Path):
    wire = tmp_path / "updates.jsonl"
    wire.write_text("")
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert parsed.turns == []
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags
    assert "schema-drift" not in parsed.flags


def test_truncated_line_flags_drift_and_does_not_raise(tmp_path: Path):
    wire = tmp_path / "updates.jsonl"
    wire.write_text('{"timestamp": 1, "params":\n')
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
    assert any(signal.reason == "unsupported_record" for signal in parsed.signals)
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags


def test_negative_uncached_input_is_drift_not_a_made_up_zero(tmp_path: Path):
    wire = tmp_path / "updates.jsonl"
    wire.write_text("{}\n")
    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "session": {
                    "inputTokens": 10,
                    "cachedReadTokens": 40,
                    "cacheCreationTokens": 0,
                    "outputTokens": 1,
                    "modelCalls": 1,
                    "primaryModelId": "grok-test",
                }
            }
        )
    )
    parsed = GrokParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
    assert parsed.agg_usage["input_tokens"] == 0
    assert parsed.model_stats == {}
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags
