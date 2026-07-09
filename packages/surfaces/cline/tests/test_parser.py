"""Byte-checked tests for ``ClineParser`` (HATS-960).

Fixture-driven assertions on a sanitized ``.messages.json`` — no real cline, no
auth. Each test pins one facet of the cline-field mapping (camelCase ``metrics``,
``modelInfo.id``, ``ts`` epoch-ms→ISO, ``tool_result``-as-user filtering, the
``usage/v1`` timeline) so drift is loud.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_cline import ClineParser, ClineProvider
from ai_hats_observe.parsers.base import ParsedTranscript, TranscriptParser
from ai_hats_observe.usage import SCHEMA_VERSION

FIXTURE = Path(__file__).parent / "fixtures" / "cline_session.messages.json"


@pytest.fixture
def parsed() -> ParsedTranscript:
    return ClineParser().parse(FIXTURE, Path("/absent.trace"))


@pytest.fixture
def usage() -> dict:
    return ClineParser().parse_usage(FIXTURE, Path("/absent.trace"))


# -- protocol / wiring --------------------------------------------------------

def test_satisfies_protocol() -> None:
    assert isinstance(ClineParser(), TranscriptParser)


def test_provider_wires_cline_parser() -> None:
    assert isinstance(ClineProvider().transcript_parser(), ClineParser)


# -- parse() -> ParsedTranscript ---------------------------------------------

def test_turns_are_user_text_only(parsed: ParsedTranscript) -> None:
    # tool_result-carrying user messages (m2/m4/m6) are filtered, not turns.
    assert [t.user_input for t in parsed.turns] == ["Fix the failing test", "Ship it"]


def test_turn_timestamps_are_iso_from_epoch_ms(parsed: ParsedTranscript) -> None:
    assert parsed.turns[0].timestamp == "2026-05-28T20:26:40"
    assert parsed.turns[1].timestamp == "2026-05-28T20:26:47"


def test_turn_response_is_last_assistant_text(parsed: ParsedTranscript) -> None:
    assert parsed.turns[0].response == "Patched and green."
    assert parsed.turns[1].response == "Shipped."


def test_turn_tools_and_model_switch(parsed: ParsedTranscript) -> None:
    assert parsed.turns[0].tools == [
        "Skill: backlog-manager",
        "Read: /proj/library/skills/backlog-manager/references/lifecycle.md",
        "⚙️ Model: glm-4.6",
        "Bash: pytest -q",
    ]
    assert parsed.turns[1].tools == []


def test_thinking_secs(parsed: ParsedTranscript) -> None:
    assert parsed.turns[0].thinking_secs == 1
    assert parsed.turns[1].thinking_secs == 0


def test_model_stats_keyed_by_model_info_id(parsed: ParsedTranscript) -> None:
    assert parsed.model_stats == {
        "glm-5.2": {"in": 2200, "out": 90, "calls": 2},
        "glm-4.6": {"in": 2700, "out": 35, "calls": 2},
    }


def test_agg_usage_maps_camelcase_metrics(parsed: ParsedTranscript) -> None:
    assert parsed.agg_usage == {
        "input_tokens": 4900,
        "output_tokens": 125,
        "cache_read_input_tokens": 3500,
        "cache_creation_input_tokens": 2900,
    }


# -- parse_usage() -> usage/v1 -----------------------------------------------

def test_usage_schema_and_session(usage: dict) -> None:
    assert usage["schema_version"] == SCHEMA_VERSION
    assert usage["source"] == "cline_session.messages.json"
    assert usage["session_id"] == "fixture-cline-0001"


def test_usage_totals(usage: dict) -> None:
    assert usage["usage_totals"] == {
        "input_tokens": 4900,
        "output_tokens": 125,
        "cache_read_input_tokens": 3500,
        "cache_creation_input_tokens": 2900,
    }


def test_usage_always_on_is_first_assistant(usage: dict) -> None:
    ao = usage["always_on"]
    assert ao["first_input_tokens"] == 1000
    assert ao["first_cache_creation_input_tokens"] == 800
    assert ao["first_cache_read_input_tokens"] == 0
    assert ao["model"] == "glm-5.2"


def test_usage_skill_load_reconstructed(usage: dict) -> None:
    assert usage["aggregates"]["skill_loads"] == {"backlog-manager": 1}
    ev = [e for e in usage["timeline"] if e["kind"] == "skill_load"]
    assert len(ev) == 1
    assert ev[0]["name"] == "backlog-manager"
    assert ev[0]["args"] == "show"
    # next assistant turn cached 1500 tokens → reconstructed attribution.
    assert ev[0]["tokens_delta"] == 1500
    assert ev[0]["tokens_attribution"] == "reconstructed"


def test_usage_reference_read_reconstructed(usage: dict) -> None:
    refs = usage["aggregates"]["reference_reads"]
    assert refs == {"/proj/library/skills/backlog-manager/references/lifecycle.md": 1}
    ev = [e for e in usage["timeline"] if e["kind"] == "reference_read"]
    assert len(ev) == 1
    assert ev[0]["tokens_delta"] == 600


def test_usage_tool_timeline_and_calls(usage: dict) -> None:
    agg = usage["aggregates"]
    assert agg["tool_calls"] == 3  # Skill + Read + Bash
    assert agg["tool_results"] == 3
    assert [e["kind"] for e in usage["timeline"]] == ["skill_load", "reference_read", "tool"]
    tool_ev = [e for e in usage["timeline"] if e["kind"] == "tool"]
    assert tool_ev[0]["name"] == "Bash"


def test_usage_tool_errors_not_derivable(usage: dict) -> None:
    agg = usage["aggregates"]
    # cline tool_result has no error marker → 0 errors, no-signal success-rate,
    # flagged so it's never read as measured.
    assert agg["tool_errors"] == 0
    assert agg["tool_success_rate"] == 1.0
    assert any("tool-errors-not-derivable" in f for f in usage["flags"])


# -- trace fallback -----------------------------------------------------------

def test_parse_falls_back_to_trace_when_absent(tmp_path: Path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text("18:15:00.000 [REQ] hello there\n")
    parsed = ClineParser().parse(None, trace)
    assert parsed.turns[0].user_input == "hello there"
    assert parsed.model_stats == {}


def test_parse_usage_falls_back_to_trace_when_absent(tmp_path: Path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text("18:15:00.000 [REQ] hi\n")
    report = ClineParser().parse_usage(None, trace)
    assert report["usage_totals"]["input_tokens"] == 0


def test_malformed_messages_json_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "x.messages.json"
    bad.write_text("{not json")
    trace = tmp_path / "trace.log"
    trace.write_text("18:15:00.000 [REQ] recover\n")
    parsed = ClineParser().parse(bad, trace)
    assert parsed.turns[0].user_input == "recover"
