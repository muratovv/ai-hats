"""A surface with no token telemetry never records a token count (HATS-1397).

The third state ``test_metrics_sensor_honesty`` misses: the sensor DID fire —
turns and tool calls are real — but one counter family is unmeasurable because
the surface never emits it. Nothing in agy's ``transcript.jsonl`` carries usage.
Live damage, session ``20260731-100500-1-40786``: a ``session-reviewer`` returned
2.9 KB of YAML verdicts, its record said ``measured: true`` beside
``tokens: {output: 0}``, and ``is_zero_output`` read that as proof of silence.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.harness.diagnostic import is_zero_output
from ai_hats.harness.guard import apply_post_run_guard
from ai_hats.pipeline.harness_policy import HarnessPolicy
from ai_hats_agy.parser import AgyParser
from ai_hats_observe import AuditWriter, Session
from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY, METRICS_JSON

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


def test_agy_parse_flags_the_missing_telemetry_on_both_reports():
    """``parse_usage`` already said so; ``parse`` — the path feeding metrics.json
    — returned a hard-zero ``agg_usage`` and no flag, so the writer had nothing
    to distinguish "no telemetry" from "measured zero". One shared constant, so
    a consumer can compare rather than substring-match a prose sentence."""
    trace = FIXTURE.parent / "absent-trace.log"

    parsed = AgyParser().parse(FIXTURE, trace)
    usage = AgyParser().parse_usage(FIXTURE, trace)

    assert parsed.turns, "the transcript is structured — turns are measurable"
    assert FLAG_NO_TOKEN_TELEMETRY in parsed.flags
    assert FLAG_NO_TOKEN_TELEMETRY in usage["flags"]


def test_measurable_counters_survive_the_unmeasurable_ones(tmp_path):
    """Turns and tool calls came off the same parse and are real; only the token
    block is unknowable, so only it is withheld. Writing it as zeros is what put
    ``tokens: {output: 0}`` next to ``measured: true`` in the live record."""
    session = agy_session(tmp_path)

    AuditWriter(AgyParser()).build(session, jsonl_path=FIXTURE)

    m = read_metrics(session)
    assert m["measured"] is True
    assert m["turns"] == 1
    assert m["tool_calls"] == 0
    assert FLAG_NO_TOKEN_TELEMETRY in m["flags"]
    assert "tokens" not in m, f"a surface with no telemetry claimed {m.get('tokens')!r}"


def test_re_enrichment_drops_the_zeros_already_written_to_disk(tmp_path):
    """Verbatim counters of the live record, which ``observe session backfill``
    re-enriches. A record carrying the flag AND a token count contradicts itself,
    and the count is the half every direct-counter consumer reads."""
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
    assert FLAG_NO_TOKEN_TELEMETRY in m["flags"]
    assert "tokens" not in m, f"stale fabricated tokens survived as {m.get('tokens')!r}"
    assert "- **tokens**" not in session.audit_path.read_text()


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
