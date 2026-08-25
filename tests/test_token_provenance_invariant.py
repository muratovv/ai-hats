"""The two reports of one session never disagree about its token counts (HATS-1433).

``metrics.json`` (via ``parse``) and ``usage.json`` (via ``parse_usage``) are
derived from the same transcript by two different code paths, and nothing used
to hold them together. They drifted twice in a row without a test noticing:
HATS-1427 left ``usage.json`` carrying a scraped count with no flag at all,
and HATS-1441 left ``metrics.json`` saying "no telemetry" beside a
``usage.json`` that showed 4200 input tokens for the same session.

The invariant below is deliberately about agreement, not about any one value:
whatever the ladder resolves, both reports must tell the same story about
*whether* there is a number and *where it came from*.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.surfaces.agy.parser import AgyParser
from ai_hats_observe.artifacts import (
    FLAG_NO_TOKEN_TELEMETRY,
    FLAG_TOKEN_TELEMETRY_ESTIMATED,
)

TURNS = [
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
        "content": "some answer with a few words in it",
    },
]

MEASURED = {"input": 150, "output": 50, "cache_read": 0, "cache_creation": 0}
TRACE_WITH_COUNTS = (
    "13:00:00.000 [REQ] some question\n"
    "13:00:02.000 [RES] ▸ Thought for 2s, 495 tokens\n"
    "13:00:05.000 [RES] (1m 10s · ↓ 4.2k tokens)\n"
)
TRACE_TEXT_ONLY = "14:00:00.000 [REQ] some question\n14:00:01.000 [RES] ⏺ some answer\n"

# (id, jsonl records or None, trace text, metrics tokens, expected flag)
CASES = [
    ("measured wins over everything", TURNS, TRACE_WITH_COUNTS, MEASURED, None),
    (
        "scraped off the rendered trace",
        TURNS,
        TRACE_WITH_COUNTS,
        None,
        FLAG_TOKEN_TELEMETRY_ESTIMATED,
    ),
    ("estimated off turn text", TURNS, TRACE_TEXT_ONLY, None, FLAG_TOKEN_TELEMETRY_ESTIMATED),
    ("nothing to measure at all", [], "", None, FLAG_NO_TOKEN_TELEMETRY),
    (
        "no jsonl, counts in the trace",
        None,
        TRACE_WITH_COUNTS,
        None,
        FLAG_TOKEN_TELEMETRY_ESTIMATED,
    ),
    ("no jsonl, text in the trace", None, TRACE_TEXT_ONLY, None, FLAG_TOKEN_TELEMETRY_ESTIMATED),
    ("no jsonl, empty trace", None, "", None, FLAG_NO_TOKEN_TELEMETRY),
]


def _session(tmp_path: Path, records, trace_text: str, metrics_tokens):
    session_dir = tmp_path / "session_20260801-120000-1-1"
    session_dir.mkdir()
    trace = session_dir / "trace.log"
    trace.write_text(trace_text)
    metrics: dict = {"schema_version": "audit/v1", "provider": "agy"}
    if metrics_tokens:
        metrics["tokens"] = metrics_tokens
    (session_dir / "metrics.json").write_text(json.dumps(metrics))
    if records is None:
        return None, trace
    jsonl = session_dir / "transcript.jsonl"
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in records))
    return jsonl, trace


def _telemetry_flag(flags) -> str | None:
    marks = [f for f in flags if f in (FLAG_NO_TOKEN_TELEMETRY, FLAG_TOKEN_TELEMETRY_ESTIMATED)]
    assert len(marks) <= 1, f"a record claims two provenances at once: {flags}"
    return marks[0] if marks else None


@pytest.mark.parametrize(
    "records,trace_text,metrics_tokens,expected_flag",
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_both_reports_agree_on_provenance(
    tmp_path, records, trace_text, metrics_tokens, expected_flag
):
    jsonl, trace = _session(tmp_path, records, trace_text, metrics_tokens)

    parsed = AgyParser().parse(jsonl, trace)
    usage = AgyParser().parse_usage(jsonl, trace)

    assert _telemetry_flag(parsed.flags) == expected_flag
    assert _telemetry_flag(usage["flags"]) == expected_flag, (
        f"metrics.json says {_telemetry_flag(parsed.flags)!r} while usage.json says "
        f"{_telemetry_flag(usage['flags'])!r} about the same session"
    )


@pytest.mark.parametrize(
    "records,trace_text,metrics_tokens,expected_flag",
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_both_reports_agree_on_the_numbers(
    tmp_path, records, trace_text, metrics_tokens, expected_flag
):
    jsonl, trace = _session(tmp_path, records, trace_text, metrics_tokens)

    parsed = AgyParser().parse(jsonl, trace)
    usage = AgyParser().parse_usage(jsonl, trace)
    agg = usage.get("aggregates")
    if agg is None:  # a report shape with no aggregates carries no claim to contradict
        return

    for key in ("input_tokens", "output_tokens"):
        assert parsed.agg_usage[key] == agg[key], (
            f"{key}: metrics.json {parsed.agg_usage[key]} vs usage.json {agg[key]}"
        )

    if expected_flag == FLAG_NO_TOKEN_TELEMETRY:
        assert parsed.agg_usage["output_tokens"] == 0, (
            "a flag saying 'nothing measured' beside a count"
        )
