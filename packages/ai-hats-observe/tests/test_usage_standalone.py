"""T15 / 0.3.0 (HATS-953) — the usage report as a parser output, standalone.

Proves a third party can build a ``usage/v2`` report from the ``ai_hats_observe``
surface alone — ``TranscriptParser.parse_usage`` — on a bare tmp dir, with no
``ai-hats.yaml`` and no composition. The Claude surface parses a transcript; the
trace surface (Gemini + JSONL-missing fallback) returns a well-formed empty
report, so usage is a per-surface output, not a Claude-only artifact.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.usage import SCHEMA_VERSION

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"


def test_claude_parser_builds_the_usage_report(tmp_path: Path) -> None:
    """ClaudeParser.parse_usage(jsonl) yields the measured usage/v2 report."""
    report = ClaudeParser().parse_usage(TRANSCRIPTS / "normal.jsonl", tmp_path / "trace.log")
    assert report["schema_version"] == SCHEMA_VERSION == "usage/v2"
    assert report["source"] == "normal.jsonl"
    # counted once per API call, not once per record — the usage/v2 rule
    assert report["usage_totals"]["input_tokens"] == 100
    assert report["api_calls"] == 1
    assert report["always_on"]["first_input_tokens"] == 100
    assert report["aggregates"]["skill_loads"] == {"backlog-manager": 1}


def test_claude_parser_falls_back_to_trace_when_no_jsonl(tmp_path: Path) -> None:
    """No JSONL → the trace-surface empty report (mirrors ClaudeParser.parse)."""
    report = ClaudeParser().parse_usage(None, tmp_path / "trace.log")
    assert report["schema_version"] == "usage/v2"
    assert "no-structured-transcript" in report["flags"]


def test_trace_parser_returns_well_formed_empty_usage(tmp_path: Path) -> None:
    """The trace surface has no token telemetry — empty but well-formed."""
    report = TraceParser().parse_usage(None, tmp_path / "trace.log")
    assert report["schema_version"] == "usage/v2"
    assert report["source"] == "trace.log"
    assert report["usage_totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert report["api_calls"] == 0
    assert report["signals"] == []
    assert report["timeline"] == []
    assert report["always_on"] is None
    assert "no-structured-transcript" in report["flags"]
