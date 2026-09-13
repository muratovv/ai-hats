"""e2e (HATS-1966)

flow:   a transcript in which one API call was split across three records, and
        a second in which the platform refused the call, are read by the
        bash-composable usage entry point
cmds:
    python -m ai_hats_observe.usage <transcript.jsonl>
expect: the split call is billed once and reported as one api_call, and the
        refusal reaches the report as a signal naming who must act
why:    every token number ai-hats published was inflated by summing a usage
        dict the CLI repeats on each fragment of one call, and a run the
        platform killed was indistinguishable from a short one
"""

# A real subprocess, because this entry point IS the contract: the retroactive
# sweep over historical transcripts calls it from bash, so an in-process call
# would not exercise the boundary the numbers cross. The two fixtures are the
# two shapes HATS-1966 measured over the local corpus — a call the CLI split
# across records, and one the platform refused — rather than invented cases.
# comment-length: allow — names why this tier is the one that can prove it

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _record(**fields) -> str:
    return json.dumps(fields)


def _fragmented_call(tmp_path: Path) -> Path:
    """One API call the CLI split across three records, each echoing its usage."""
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 40,
    }
    lines = [
        _record(
            type="user",
            uuid="u-1",
            timestamp="2026-09-01T10:00:00.000Z",
            message={"role": "user", "content": [{"type": "text", "text": "do the thing"}]},
        ),
        _record(
            type="assistant",
            uuid="a-1",
            requestId="req_split",
            timestamp="2026-09-01T10:00:01.000Z",
            message={
                "id": "msg_split",
                "model": "claude-opus-5",
                "usage": usage,
                "content": [{"type": "thinking", "thinking": "considering"}],
            },
        ),
        _record(
            type="assistant",
            uuid="a-2",
            requestId="req_split",
            timestamp="2026-09-01T10:00:02.000Z",
            message={
                "id": "msg_split",
                "model": "claude-opus-5",
                "usage": usage,
                "content": [{"type": "text", "text": "here is the answer"}],
            },
        ),
        _record(
            type="assistant",
            uuid="a-3",
            requestId="req_split",
            timestamp="2026-09-01T10:00:03.000Z",
            message={
                "id": "msg_split",
                "model": "claude-opus-5",
                "usage": usage,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": " and its conclusion"}],
            },
        ),
    ]
    path = tmp_path / "split.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _refused_call(tmp_path: Path) -> Path:
    """A call the platform refused, whose prose used to become the answer."""
    lines = [
        _record(
            type="user",
            uuid="u-1",
            timestamp="2026-09-01T10:00:00.000Z",
            message={"role": "user", "content": [{"type": "text", "text": "go"}]},
        ),
        _record(
            type="assistant",
            uuid="a-1",
            timestamp="2026-09-01T10:00:01.000Z",
            isApiErrorMessage=True,
            error="rate_limit",
            apiErrorStatus=429,
            quotaLimits={"status": "rejected", "resetsAt": 1788529800},
            message={
                "id": "msg_err",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "You've hit your monthly spend limit"}],
            },
        ),
    ]
    path = tmp_path / "refused.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _report(path: Path) -> dict:
    run = subprocess.run(
        [sys.executable, "-m", "ai_hats_observe.usage", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, f"entry point failed: {run.stderr}"
    return json.loads(run.stdout)


def test_a_call_split_across_records_is_billed_once(tmp_path: Path) -> None:
    """Three records, one call. Summing them is the defect this card exists for.

    Positive control: the cache counters are asserted too, so a report that
    zeroed the totals instead of de-duplicating them would not pass.
    """
    report = _report(_fragmented_call(tmp_path))

    assert report["schema_version"] == "usage/v2"
    assert report["api_calls"] == 1, "three fragments were counted as three calls"
    totals = report["usage_totals"]
    assert totals["input_tokens"] == 100
    assert totals["output_tokens"] == 50
    # POSITIVE CONTROL: the numbers are present, not merely not-tripled
    assert totals["cache_read_input_tokens"] == 900
    assert totals["cache_creation_input_tokens"] == 40


def test_a_refusal_reaches_the_report_as_a_signal(tmp_path: Path) -> None:
    """The run was killed by the platform, and the report has to say so.

    Positive control: the clean transcript in the sibling test raises no signal,
    so this cannot pass by reporting a signal unconditionally.
    """
    report = _report(_refused_call(tmp_path))
    signals = report["signals"]

    assert len(signals) == 1, f"expected one signal, got {signals}"
    signal = signals[0]
    assert signal["obligation"] == "harness_must_act"
    assert signal["kind"] == "wait"
    assert signal["retry_after"] == 1788529800
    assert "monthly spend limit" in (signal["detail"] or "")

    # POSITIVE CONTROL: a healthy transcript stays silent on this axis
    assert _report(_fragmented_call(tmp_path))["signals"] == []
