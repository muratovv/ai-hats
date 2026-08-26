"""A surface with no token telemetry never passes a number off as measured
(HATS-1397, contract widened by HATS-1433).

The third state ``test_metrics_sensor_honesty`` misses: the sensor DID fire —
turns and tool calls are real — but one counter family is unmeasurable because
the surface never emits it. Nothing in agy's ``transcript.jsonl`` carries usage.
Live damage, session ``20260731-100500-1-40786``: a ``session-reviewer`` returned
2.9 KB of YAML verdicts, its record said ``measured: true`` beside
``tokens: {output: 0}``, and ``is_zero_output`` read that as proof of silence.

HATS-1427 then began recovering a count by scraping the rendered trace and
estimating off text length. That is allowed to be written — HATS-1433 only
insists the record says which of the two it is, because every consumer
downstream reads an unqualified number as fact.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.harness.diagnostic import is_zero_output
from ai_hats.harness.guard import apply_post_run_guard
from ai_hats.pipeline.harness_policy import HarnessPolicy
from ai_hats.surfaces.agy.parser import AgyParser
from ai_hats_observe import AuditWriter, Session
from ai_hats_observe.artifacts import (
    FLAG_NO_TOKEN_TELEMETRY,
    FLAG_TOKEN_TELEMETRY_ESTIMATED,
    METRICS_JSON,
)

FIXTURE = Path(__file__).parent / "fixtures" / "agy_jsonl" / "one_turn_no_telemetry.jsonl"


def agy_session(tmp_path: Path, metrics: dict | None = None) -> Session:
    """A session dir as ``finalize_audit`` leaves it just before enrichment."""
    session_dir = tmp_path / "session_20260731-100500-1-40786"
    session_dir.mkdir()
    base = {
        "schema_version": "audit/v1",
        "finalized": True,
        "exit_code": 0,
        "role": "session-reviewer",
        "provider": "agy",
    }
    base.update(metrics or {})
    (session_dir / METRICS_JSON).write_text(json.dumps(base))
    return Session(session_id="20260731-100500-1-40786", session_dir=session_dir)


def read_metrics(session: Session) -> dict:
    return json.loads(session.metrics_path.read_text())


def test_agy_parse_flags_the_estimate_on_both_reports():
    """``parse`` — the path feeding metrics.json — used to return a hard-zero
    ``agg_usage`` and no flag, so the writer had nothing to distinguish "no
    telemetry" from "measured zero". HATS-1433 keeps that shared-constant
    argument and splits the third state out: this fixture carries turn text, so
    the HATS-1427 recovery ladder estimates a count off it — and an estimate has
    to say so, or it is read as the measurement this surface never makes."""
    trace = FIXTURE.parent / "absent-trace.log"

    parsed = AgyParser().parse(FIXTURE, trace)
    usage = AgyParser().parse_usage(FIXTURE, trace)

    assert parsed.turns, "the transcript is structured — turns are measurable"
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in parsed.flags
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in usage["flags"]
    assert FLAG_NO_TOKEN_TELEMETRY not in parsed.flags, "an estimate is not an absence"


def test_an_estimated_count_never_lands_unmarked(tmp_path):
    """Turns and tool calls came off the same parse and are real; the token block
    is a guess off string lengths. It may be written — but never beside a bare
    ``measured: true``, which is the shape that put ``tokens: {output: 0}`` into
    the live record as fact."""
    session = agy_session(tmp_path)

    AuditWriter(AgyParser()).build(session, jsonl_path=FIXTURE)

    m = read_metrics(session)
    assert m["measured"] is True
    assert m["turns"] == 1
    assert m["tool_calls"] == 0
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in m["flags"], (
        f"tokens {m.get('tokens')!r} reached metrics.json with flags "
        f"{m.get('flags')!r} — indistinguishable from a measurement"
    )


def test_re_enrichment_relabels_the_zeros_already_written_to_disk(tmp_path):
    """Verbatim counters of the live record, which ``observe session backfill``
    re-enriches. The pre-fix record carries a token count and an empty ``flags``;
    re-enrichment must not leave that claim standing unqualified."""
    session = agy_session(
        tmp_path,
        {
            "measured": True,
            "flags": [],
            "turns": 1,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
            "models": {},
            "tool_calls": 0,
        },
    )

    AuditWriter(AgyParser()).build(session, jsonl_path=FIXTURE, keep_raw=True)

    m = read_metrics(session)
    assert m["turns"] == 1
    assert FLAG_TOKEN_TELEMETRY_ESTIMATED in m["flags"]
    assert m["tokens"]["output"] > 0, "the stale fabricated zeros survived re-enrichment"


def test_an_estimate_never_gets_a_run_discarded(tmp_path):
    """Same defense-in-depth as the flag below, one state over: ``is_zero_output``
    decides whether a sub-agent's work is thrown away, and an estimate is not the
    measurement that decision needs."""
    estimated = {
        "exit_code": 0,
        "role": "session-reviewer",
        "provider": "agy",
        "measured": True,
        "flags": [FLAG_TOKEN_TELEMETRY_ESTIMATED],
        "turns": 1,
        "tokens": {"input": 12, "output": 0, "cache_read": 0, "cache_creation": 0},
        "models": {},
        "tool_calls": 0,
    }

    assert is_zero_output(estimated) is False


def test_the_guard_reads_the_flag_not_only_the_missing_key():
    """Defense in depth at the step that destroys work: ``is_zero_output`` decides
    whether a sub-agent's output is discarded, so it must not owe its correctness
    to a distant writer's key omission. The shape below — flag plus the zeros the
    pre-fix writer emitted — is what any writer that flags without withholding
    would produce, and it is the live record that cost PROP-170 its verdicts."""
    flagged_but_zeroed = {
        "exit_code": 0,
        "role": "session-reviewer",
        "provider": "agy",
        "measured": True,
        "flags": [FLAG_NO_TOKEN_TELEMETRY],
        "turns": 1,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
        "models": {},
        "tool_calls": 0,
    }

    assert is_zero_output(flagged_but_zeroed) is False


def test_a_reviewer_that_answered_in_prose_is_not_declared_silent(tmp_path):
    """The incident, whole: parser → metrics.json → guard, no stub in between.

    The reviewer's only output was prose (0 tool calls) on a surface that counts
    no tokens, so every counter the guard consults was structurally 0 while the
    run in fact returned 2.9 KB of verdicts. Pre-fix this raised
    ``HarnessZeroOutputError`` and the verdicts were dropped for PROP-170."""
    session = agy_session(tmp_path)

    AuditWriter(AgyParser()).build(session, jsonl_path=FIXTURE)

    apply_post_run_guard(session, HarnessPolicy(reporting=True, on_zero_output="harness_incident"))
    assert read_metrics(session)["turns"] == 1
