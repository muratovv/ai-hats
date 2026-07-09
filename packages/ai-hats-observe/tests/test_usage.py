"""Unit tests for the pure transcript usage parser (HATS-664).

Fixture-driven dict-out assertions — no live session, no claude binary. Each
test pins one behaviour of ``usage/v1`` so drift is loud.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_observe.usage import SCHEMA_VERSION, parse_session_usage

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"


@pytest.fixture
def normal() -> dict:
    return parse_session_usage(TRANSCRIPTS / "normal.jsonl")


def test_schema_and_source(normal: dict) -> None:
    assert normal["schema_version"] == SCHEMA_VERSION
    assert normal["source"] == "normal.jsonl"
    assert normal["session_id"] == "sess-normal"


def test_session_meta_null_in_pure_parser(normal: dict) -> None:
    """role/provider/exit_code are ai-hats metadata — not transcript-derived,
    so the pure parser leaves them null (the step fills them)."""
    assert normal["role"] is None
    assert normal["provider"] is None
    assert normal["exit_code"] is None


def test_usage_totals(normal: dict) -> None:
    # input: 100+200+300+350+360 ; output: 50+30+40+20+15
    assert normal["usage_totals"] == {
        "input_tokens": 1310,
        "output_tokens": 155,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 10100,
    }


def test_always_on_is_first_assistant(normal: dict) -> None:
    ao = normal["always_on"]
    assert ao["first_input_tokens"] == 100
    assert ao["first_cache_creation_input_tokens"] == 8000
    assert ao["first_cache_read_input_tokens"] == 2000
    assert ao["model"] == "claude-x"


def test_skill_load_event_and_aggregate(normal: dict) -> None:
    assert normal["aggregates"]["skill_loads"] == {"backlog-manager": 1}
    skill_events = [e for e in normal["timeline"] if e["kind"] == "skill_load"]
    assert len(skill_events) == 1
    assert skill_events[0]["name"] == "backlog-manager"
    # reconstructed attribution: next assistant turn cached 1500 tokens.
    assert skill_events[0]["tokens_delta"] == 1500
    assert skill_events[0]["tokens_attribution"] == "reconstructed"


def test_reference_read_event_and_attribution(normal: dict) -> None:
    refs = normal["aggregates"]["reference_reads"]
    assert refs == {"/proj/library/skills/backlog-manager/references/lifecycle.md": 1}
    ref_events = [e for e in normal["timeline"] if e["kind"] == "reference_read"]
    assert len(ref_events) == 1
    assert ref_events[0]["tokens_delta"] == 600


def test_tool_calls_and_success_rate(normal: dict) -> None:
    agg = normal["aggregates"]
    assert agg["tool_calls"] == 4  # Skill + Read + 2 Bash
    assert agg["tool_results"] == 4
    assert agg["tool_errors"] == 1
    assert agg["tool_success_rate"] == 0.75


def test_stop_hook_event(normal: dict) -> None:
    agg = normal["aggregates"]
    assert agg["hook_firings"] == 1
    assert agg["hook_total_ms"] == 142
    hooks = [e for e in normal["timeline"] if e["kind"] == "stop_hook"]
    assert hooks[0]["name"] == "stop-hook.sh"
    assert hooks[0]["duration_ms"] == 142


def test_normal_has_no_flags(normal: dict) -> None:
    assert normal["flags"] == []
    assert normal["sidechain"]["is_sidechain"] is False


def test_fail_soft_on_malformed() -> None:
    report = parse_session_usage(TRANSCRIPTS / "malformed.jsonl")
    # Two bad lines: "this is not json at all" + bare "42" (not a dict).
    assert any("malformed-lines: 2" in f for f in report["flags"])
    # Unknown entry type flagged, not crashed.
    assert any("weird-new-type" in f for f in report["flags"])
    # Valid entries still parsed despite the noise.
    assert report["usage_totals"]["cache_creation_input_tokens"] == 100
    assert report["session_id"] == "sess-mal"


def test_sidechain_detect_and_link() -> None:
    report = parse_session_usage(TRANSCRIPTS / "sidechain.jsonl")
    sc = report["sidechain"]
    assert sc["is_sidechain"] is True
    assert sc["agent_name"] == "Explore"
    assert sc["parent_session_id"] == "parent-uuid-123"


def test_missing_file_is_fail_soft() -> None:
    report = parse_session_usage(TRANSCRIPTS / "does-not-exist.jsonl")
    assert any("unreadable" in f for f in report["flags"])
    assert report["timeline"] == []


def test_success_rate_none_when_no_results() -> None:
    report = parse_session_usage(TRANSCRIPTS / "sidechain.jsonl")
    # No tool_result entries → success-rate is None (distinct from 0.0).
    assert report["aggregates"]["tool_success_rate"] is None
