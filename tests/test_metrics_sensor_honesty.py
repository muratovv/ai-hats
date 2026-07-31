"""``metrics.json`` sensor honesty — audit/v1 counters never lie (HATS-1374, RC-A).

The pinned contract, per ``rule_composition_value_contract §3`` ("never a magic
0" — the sibling ``usage/v1`` report already honors it): sensor fired → counters
+ ``measured: true``; nothing measured → counters **absent**, ``measured: false``,
``flags`` says why; values already in the file are never clobbered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats_observe import AuditWriter, Session
from ai_hats_observe.artifacts import METRICS_JSON, is_measured

FIXTURE = Path(__file__).parent / "fixtures" / "claude_jsonl" / "three_turns_with_tool.jsonl"


def make_session(tmp_path, metrics: dict | None = None) -> Session:
    """A session dir with a base metrics.json, as ``finalize_audit`` leaves it."""
    session_dir = tmp_path / "session_20260730-120000-1"
    session_dir.mkdir()
    session = Session(session_id="20260730-120000-1", session_dir=session_dir)
    base = {"role": "maintainer", "provider": "claude", "exit_code": 0}
    base.update(metrics or {})
    (session_dir / METRICS_JSON).write_text(json.dumps(base))
    return session


def read_metrics(session: Session) -> dict:
    return json.loads(session.metrics_path.read_text())


# ------------------------------------------------------------- sensor fires


def test_structured_parse_writes_nonzero_counters_and_marks_measured(tmp_path):
    """The B7 ask: a test that fires when the sensor fires.

    Fixture is a real scrubbed Claude JSONL (2 turns, one ``Bash`` tool call,
    180 in / 43 out / 260 cache-read). Every number here is the parse's, not a
    hand-written metrics fixture — ``tests/fixtures/real_session/metrics.json``
    is synthetic per its own README and cannot catch a dead sensor.
    """
    session = make_session(tmp_path)

    AuditWriter().build(session, jsonl_path=FIXTURE)

    m = read_metrics(session)
    assert m["measured"] is True
    assert m["turns"] == 2
    assert m["tool_calls"] == 1
    assert m["tokens"]["input"] == 180
    assert m["tokens"]["output"] == 43
    assert m["tokens"]["cache_read"] == 260
    assert m["models"]["claude-opus-4-7"]["calls"] == 3


# --------------------------------------------------------- honest absence


def test_unreachable_transcript_omits_counters_instead_of_writing_zeros(tmp_path):
    """No JSONL and no trace.log → nothing was measured, so nothing is claimed.

    RC-A regression guard: the pre-fix writer emitted ``turns: 0``,
    ``tool_calls: 0`` and an all-zero ``tokens`` block here, which downstream
    consumers (auto_retro thresholds, ``is_productive``, ``is_zero_output``)
    read as a measured zero.
    """
    session = make_session(tmp_path)

    AuditWriter().build(session, jsonl_path=None)

    m = read_metrics(session)
    assert m["measured"] is False
    assert "no-structured-transcript" in m["flags"]
    for absent in ("turns", "tokens", "models", "tool_calls"):
        assert absent not in m, (
            f"{absent!r} must be absent when nothing was measured, got {m[absent]!r}"
        )


def test_unmeasurable_parse_preserves_sdk_ground_truth(tmp_path):
    """The headline contradiction from the verdict, as a test.

    ``experiments/hatrack-hardening/control/runs/new/run-1/sessions/
    session_20260719-154405-1/metrics.json`` really holds ``num_turns: 5`` and
    ``total_cost_usd: 0.0286`` next to ``turns: 0`` and an all-zero ``tokens``
    block: the SDK reported 5 turns and 2.9 cents, then the parser wrote zeros
    over it in the same file. A file may not contradict itself.
    """
    session = make_session(
        tmp_path,
        {"claude_session_id": "7f7f4497", "num_turns": 5, "total_cost_usd": 0.0286019},
    )

    AuditWriter().build(session, jsonl_path=None)

    m = read_metrics(session)
    assert m["num_turns"] == 5
    assert m["total_cost_usd"] == pytest.approx(0.0286019)
    assert "turns" not in m, "SDK reported 5 turns — a fabricated turns:0 contradicts the same file"


def test_audit_md_does_not_assert_an_unmeasured_turn_count(tmp_path):
    """The honesty contract must reach audit.md, not stop at metrics.json.

    Found by inspecting a real live session: metrics.json said ``measured:
    false`` while audit.md's ``## Metrics`` block still printed
    ``- **turns**: 0``. audit.md is what a human and the session-reviewer read,
    so that is the same fabrication in the artifact that matters most.
    """
    session = make_session(tmp_path)

    AuditWriter().build(session, jsonl_path=None)

    audit = session.audit_path.read_text()
    assert "- **turns**: 0" not in audit, audit
    assert "- **measured**: false" in audit
    assert "no-structured-transcript" in audit


def test_audit_md_still_reports_a_measured_turn_count(tmp_path):
    session = make_session(tmp_path)

    AuditWriter().build(session, jsonl_path=FIXTURE)

    audit = session.audit_path.read_text()
    assert "- **turns**: 2" in audit
    assert "- **measured**: false" not in audit


def test_audit_md_never_contradicts_itself(tmp_path):
    """F3: the record already HAS counters — the case the 1374 test omitted.

    ``_format_audit`` ran before ``_write_metrics``, read metrics.json in its old
    state, and dumped every non-header key verbatim, so a stale ``turns: 42``
    printed beside the fresh ``measured: false`` it contradicts. The 1374 test
    passed because its fixture had no ``turns`` at all.
    """
    session = make_session(tmp_path, {"measured": True, "turns": 42, "tool_calls": 216})

    AuditWriter().build(session, jsonl_path=None)

    audit = session.audit_path.read_text()
    assert audit.count("- **measured**") == 1, audit
    assert audit.count("- **turns**") == 1, audit
    # Counters survived (S6), so the document must agree they did.
    assert "- **measured**: true" in audit.lower()
    assert "- **turns**: 42" in audit


def test_legacy_fabricated_zeros_are_dropped_not_preserved(tmp_path):
    """Re-enriching a pre-HATS-1374 record must not keep its fabricated zeros.

    Caught only by running the writer over a real artifact: the synthetic cases
    above have no counters to begin with, so they never exercised this. Verbatim
    shape of ``experiments/hatrack-hardening/control/runs/new/run-1/sessions/
    session_20260719-154405-1/metrics.json``. Leaving ``turns: 0`` next to
    ``measured: false`` keeps every direct-counter consumer lying — it is what
    still raised a bogus zero-output incident for this session.
    """
    session = make_session(
        tmp_path,
        {
            "num_turns": 5,
            "total_cost_usd": 0.0286019,
            "turns": 0,
            "tool_calls": 0,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
            "models": {},
        },
    )

    AuditWriter().build(session, jsonl_path=None)

    m = read_metrics(session)
    assert m["measured"] is False
    for absent in ("turns", "tokens", "models", "tool_calls"):
        assert absent not in m, f"stale fabricated {absent!r} survived as {m.get(absent)!r}"
    assert m["num_turns"] == 5, "SDK telemetry is not a fabrication — it stays"


def test_legacy_real_counters_survive_an_unreachable_transcript(tmp_path):
    """The other half of the legacy case — and the one that destroyed data (F4).

    Verbatim shape of ``session_20260529-093014-1``: a pre-HATS-1374 record has
    no ``measured`` key at all, so "reset anything without ``measured: true``"
    erased genuine measurements. ``is_measured`` called the very same record
    measured, so reader and writer disagreed by construction and the destructive
    one won. ``session backfill --all --force`` would have run this over ~1005
    archived sessions.
    """
    session = make_session(
        tmp_path,
        {
            "turns": 2,
            "tool_calls": 6,
            "tokens": {
                "input": 16378,
                "output": 2075,
                "cache_read": 314832,
                "cache_creation": 51791,
            },
            "models": {
                "claude-opus-4-8": {"calls": 11, "input_tokens": 16378, "output_tokens": 2075}
            },
        },
    )

    AuditWriter().build(session, jsonl_path=None)

    m = read_metrics(session)
    assert m["turns"] == 2
    assert m["tool_calls"] == 6
    assert m["tokens"]["cache_read"] == 314832
    assert m["models"]["claude-opus-4-8"]["calls"] == 11
    assert "no-structured-transcript" in m["flags"], "this run's failure is still recorded"


def test_reader_and_writer_agree_on_what_a_measurement_is(tmp_path):
    """The invariant behind F4: one predicate, not two that can diverge.

    Whatever ``is_measured`` calls a measurement, ``_write_metrics`` must keep;
    whatever it calls a fabrication, the writer may reset. Asserted over the
    record shapes that actually occur on disk, legacy ones included.
    """
    shapes = [
        {},
        {"turns": 0, "tool_calls": 0, "tokens": {"input": 0, "output": 0}},
        {"turns": 2, "tool_calls": 6, "tokens": {"input": 16378, "output": 2075}},
        {"measured": True, "turns": 7, "tool_calls": 12},
        {"measured": False, "flags": ["sensor-error"]},
        {"turns": 0, "tool_calls": 0, "tokens": {"input": 0, "output": 121}},
    ]
    for i, shape in enumerate(shapes):
        case = tmp_path / f"case{i}"
        case.mkdir()
        session = make_session(case, shape)
        claimed = is_measured({**{"role": "maintainer"}, **shape})

        AuditWriter().build(session, jsonl_path=None)

        kept = "turns" in read_metrics(session)
        assert kept == claimed, f"reader and writer disagree on {shape!r}"


def test_malformed_prior_flags_are_dropped_not_propagated(tmp_path):
    """A non-list ``flags`` in the record must not crash or leak into the union.

    Covers the tolerant read in ``_merge_flags`` — an untested fallback is the
    D1-4 class this card exists to stop, so the tolerance is pinned, not assumed.
    """
    session = make_session(tmp_path, {"flags": "not-a-list"})

    AuditWriter().build(session, jsonl_path=None)

    assert read_metrics(session)["flags"] == ["no-structured-transcript"]


def test_unmeasurable_parse_does_not_clobber_an_earlier_measurement(tmp_path):
    """Re-running the writer without a transcript must not destroy real numbers.

    Makes the backfill sweep safe: a session whose JSONL has since been pruned
    keeps the counters an earlier successful parse recorded.
    """
    session = make_session(
        tmp_path,
        {"measured": True, "turns": 7, "tool_calls": 12, "flags": []},
    )

    AuditWriter().build(session, jsonl_path=None)

    m = read_metrics(session)
    assert m["turns"] == 7
    assert m["tool_calls"] == 12
    assert m["measured"] is True, "counters survived, so the record stays a measured one"
    assert "no-structured-transcript" in m["flags"], "this run's failure is still recorded"
