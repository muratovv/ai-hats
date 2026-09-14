"""Unit tests for the pure transcript usage parser (HATS-664, HATS-1966 S5).

Fixture-driven dict-out assertions — no live session, no claude binary. Each
test pins one behaviour of ``usage/v2`` so drift is loud. The counting rule is
what the version marks: a JSONL record is a *fragment* of an API call, so cost
is counted once per call and ``api_calls`` counts calls, not records.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats_observe.usage import SCHEMA_VERSION, parse_session_usage

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"


@pytest.fixture
def normal() -> dict:
    return parse_session_usage(TRANSCRIPTS / "normal.jsonl")


@pytest.fixture
def fragmented() -> dict:
    """One call (``req_A``) spread over three records carrying an identical
    ``usage``, followed by a second, distinct call (``req_B``)."""
    return parse_session_usage(TRANSCRIPTS / "fragments.jsonl")


def test_schema_and_source(normal: dict) -> None:
    assert normal["schema_version"] == SCHEMA_VERSION == "usage/v2"
    assert normal["source"] == "normal.jsonl"
    assert normal["session_id"] == "sess-normal"


def test_session_meta_null_in_pure_parser(normal: dict) -> None:
    """role/provider/exit_code are ai-hats metadata — not transcript-derived,
    so the pure parser leaves them null (the step fills them)."""
    assert normal["role"] is None
    assert normal["provider"] is None
    assert normal["exit_code"] is None


# --- the counting rule -----------------------------------------------------


def test_one_call_is_counted_once_however_many_records_it_spans(fragmented: dict) -> None:
    """The headline ``usage/v2`` correction. ``req_A``'s three records each
    repeat the same usage; counting records inflated the totals 2.61x.

    Positive control: ``req_B``'s 7 input tokens are in the total, so the test
    cannot be passed by suppressing repeats — a parser that counted only the
    first usage it ever saw would report 1000, not 1007.
    """
    assert fragmented["usage_totals"] == {
        "input_tokens": 1007,  # 1000 once for req_A (not 3x) + 7 for req_B
        "output_tokens": 103,  # 100 + 3
        "cache_read_input_tokens": 5000,  # 5000 once, not 15000
        "cache_creation_input_tokens": 1500,
    }


def test_api_calls_counts_calls_not_records(fragmented: dict) -> None:
    """Four assistant records, two inference calls — and cost is proportional
    to the latter. Positive control: the record count (4) and the tool-call
    count (1) are both different numbers, so 2 cannot come from either."""
    assert fragmented["api_calls"] == 2
    assert fragmented["entry_types_seen"]["assistant"] == 4
    assert fragmented["aggregates"]["tool_calls"] == 1


def test_usage_totals(normal: dict) -> None:
    """``normal.jsonl`` carries no ``requestId`` / ``message.id`` / ``uuid``, so
    every fragment resolves to the same identity and the transcript reads as one
    call — which is exactly the rule under test, applied to a fixture that
    predates it."""
    assert normal["usage_totals"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 8000,
    }
    assert normal["api_calls"] == 1


def test_always_on_is_first_call(normal: dict) -> None:
    ao = normal["always_on"]
    assert ao["first_input_tokens"] == 100
    assert ao["first_cache_creation_input_tokens"] == 8000
    assert ao["first_cache_read_input_tokens"] == 2000
    assert ao["model"] == "claude-x"


# --- signals ---------------------------------------------------------------


def test_api_error_reaches_the_report_as_a_signal() -> None:
    """A rate-limit record becomes one signal carrying its obligation — the
    harness may act on it without a person — plus when the wall lifts and the
    surface's own code."""
    report = parse_session_usage(TRANSCRIPTS / "api_error.jsonl")

    assert len(report["signals"]) == 1
    signal = report["signals"][0]
    assert signal["obligation"] == "harness_must_act"
    assert signal["kind"] == "wait"
    assert signal["retry_after"] == 1780000000
    assert signal["raw_code"] == "429"
    assert signal["ts"] == "2026-06-06T10:00:02Z"
    # Run health is not parse quality: the file parsed cleanly.
    assert report["flags"] == []


def test_a_clean_transcript_raises_no_signal(normal: dict) -> None:
    """Positive control for the test above: the same field is empty on a
    transcript with nothing wrong, so a signal there means something happened."""
    assert normal["signals"] == []


def test_an_unmodelled_record_is_reported_not_dropped() -> None:
    """R5. The drift axis: an unknown record type is a signal, and the same
    fact still reaches ``flags`` as parse quality."""
    report = parse_session_usage(TRANSCRIPTS / "malformed.jsonl")

    kinds = {s["kind"] for s in report["signals"]}
    codes = {s["raw_code"] for s in report["signals"]}
    assert kinds == {"unsupported_record"}
    assert "weird-new-type" in codes
    assert any("weird-new-type" in f for f in report["flags"])


# --- timeline and aggregates -----------------------------------------------


def test_skill_load_event_and_aggregate(fragmented: dict) -> None:
    assert fragmented["aggregates"]["skill_loads"] == {"backlog-manager": 1}
    skill_events = [e for e in fragmented["timeline"] if e["kind"] == "skill_load"]
    assert len(skill_events) == 1
    assert skill_events[0]["name"] == "backlog-manager"
    # reconstructed attribution: the NEXT call cached 1500 tokens.
    assert skill_events[0]["tokens_delta"] == 1500
    assert skill_events[0]["tokens_attribution"] == "reconstructed"


def test_reference_read_event(normal: dict) -> None:
    refs = normal["aggregates"]["reference_reads"]
    assert refs == {"/proj/library/skills/backlog-manager/references/lifecycle.md": 1}
    ref_events = [e for e in normal["timeline"] if e["kind"] == "reference_read"]
    assert len(ref_events) == 1
    # One call in this fixture, so no later cache_creation exists to attribute:
    # absent stays None, never a magic 0.
    assert ref_events[0]["tokens_delta"] is None


def test_tool_calls_and_success_rate(normal: dict) -> None:
    agg = normal["aggregates"]
    assert agg["tool_calls"] == 4  # Skill + Read + 2 Bash
    assert agg["tool_results"] == 4
    assert agg["tool_errors"] == 1
    assert agg["tool_success_rate"] == 0.75


def test_timeline_is_a_chronology(normal: dict) -> None:
    stamps = [e["ts"] for e in normal["timeline"]]
    assert stamps == sorted(stamps)


def test_stop_hook_event(normal: dict) -> None:
    """Harness bookkeeping the event model deliberately says nothing about, so
    the report reads it off the record itself."""
    agg = normal["aggregates"]
    assert agg["hook_firings"] == 1
    assert agg["hook_total_ms"] == 142
    hooks = [e for e in normal["timeline"] if e["kind"] == "stop_hook"]
    assert hooks[0]["name"] == "stop-hook.sh"
    assert hooks[0]["duration_ms"] == 142


def test_normal_has_no_flags(normal: dict) -> None:
    assert normal["flags"] == []
    assert normal["sidechain"]["is_sidechain"] is False


# --- fail-soft -------------------------------------------------------------


def test_entry_types_seen_counts_every_record() -> None:
    """Complete now that every record type is classified — so a type outside the
    known set means real drift instead of firing on 59.4% of sessions."""
    report = parse_session_usage(TRANSCRIPTS / "normal.jsonl")
    assert report["entry_types_seen"] == {"user": 5, "assistant": 5, "system": 2}


def test_fail_soft_on_malformed() -> None:
    report = parse_session_usage(TRANSCRIPTS / "malformed.jsonl")
    # Two bad lines: "this is not json at all" + bare "42" (not a dict).
    assert any("malformed-lines: 2" in f for f in report["flags"])
    # Valid entries still parsed despite the noise.
    assert report["usage_totals"]["cache_creation_input_tokens"] == 100
    assert report["session_id"] == "sess-mal"


def test_sidechain_detect_and_link() -> None:
    report = parse_session_usage(TRANSCRIPTS / "sidechain.jsonl")
    sc = report["sidechain"]
    assert sc["is_sidechain"] is True
    assert sc["agent_name"] == "Explore"
    assert sc["parent_session_id"] == "parent-uuid-123"


def test_odd_tool_arguments_do_not_sink_the_parse(tmp_path: Path) -> None:
    """A tool input is whatever the model sent — a ``file_path`` that is a
    number, a ``skill`` that is a dict. Positive control: the well-formed Bash
    call in the same record still reaches the timeline, so this is not passing
    by dropping the record."""
    record = {
        "type": "assistant",
        "sessionId": "sess-odd",
        "requestId": "req_odd",
        "timestamp": "2026-06-06T12:00:00Z",
        "message": {
            "model": "claude-x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [
                {"type": "tool_use", "id": "x1", "name": "Read", "input": {"file_path": 7}},
                {"type": "tool_use", "id": "x2", "name": "Skill", "input": {"skill": {"a": 1}}},
                {"type": "tool_use", "id": "x3", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    path = tmp_path / "odd.jsonl"
    path.write_text(json.dumps(record) + "\n")

    report = parse_session_usage(path)

    assert report["aggregates"]["tool_calls"] == 3
    assert [e["name"] for e in report["timeline"] if e["kind"] == "tool"] == ["Read", "Bash"]
    assert report["flags"] == []


def test_missing_file_is_fail_soft() -> None:
    report = parse_session_usage(TRANSCRIPTS / "does-not-exist.jsonl")
    assert any("unreadable" in f for f in report["flags"])
    assert report["timeline"] == []


def test_success_rate_none_when_no_results() -> None:
    report = parse_session_usage(TRANSCRIPTS / "sidechain.jsonl")
    # No tool_result entries → success-rate is None (distinct from 0.0).
    assert report["aggregates"]["tool_success_rate"] is None
