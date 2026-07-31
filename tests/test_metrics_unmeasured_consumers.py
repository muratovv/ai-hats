"""Consumers must read "unmeasured" as unknown, never as zero (HATS-1374, S4).

The writer fix alone is not enough: the judge's damage list is all downstream —
a real 117s session skipped as "turns=0<5", false-positive zero-output
incidents, sessions vanishing from ``--productive``. Three of those consumers
turn out to be fixed by the writer change alone (they already branch on key
absence); those are pinned here so the fix cannot silently regress.
"""

from __future__ import annotations

import json

import pytest

from ai_hats.harness.diagnostic import is_zero_output
from ai_hats.retro.auto_retro import should_run
from ai_hats.retro.facts import _parse_metrics
from ai_hats_observe import Session
from ai_hats_observe.artifacts import METRICS_JSON, is_measured

UNMEASURED = {
    "role": "maintainer",
    "provider": "agy",
    "exit_code": 0,
    "duration_s": 117.509,
    "measured": False,
    "flags": ["no-structured-transcript"],
}
MEASURED = {"role": "maintainer", "provider": "claude", "exit_code": 0, "measured": True}


# ------------------------------------------------------------- is_measured


@pytest.mark.parametrize(
    "metrics,expected",
    [
        (UNMEASURED, False),
        ({**MEASURED, "turns": 7, "tool_calls": 12}, True),
        ({**MEASURED, "turns": 0, "tool_calls": 0}, True),
        # measured:True with a stale flag — an earlier parse's counters survived
        # a later transcript-less run, so the counters are still real.
        ({"measured": True, "turns": 7, "flags": ["no-structured-transcript"]}, True),
        # Pre-HATS-1374 record, no `measured` key. All-zero is exactly the shape
        # a failed parse wrote, so it reads as unmeasured (HATS-1397) — calling
        # it measured is what kept raising bogus zero-output incidents.
        ({"turns": 0, "tool_calls": 0}, False),
        # Same vintage, but carrying a value no failed parse could have invented.
        ({"turns": 0, "tool_calls": 0, "tokens": {"output": 121}}, True),
        ({"role": "maintainer"}, False),
    ],
)
def test_is_measured_truth_table(metrics, expected):
    assert is_measured(metrics) is expected


# ----------------------------------------------------- auto_retro threshold


def _config(tmp_path, policy: str = "smart") -> "object":
    path = tmp_path / "ai-hats.yaml"
    path.write_text(f"feedback:\n  session_retro:\n    policy: {policy}\n")
    return path


def test_unmeasured_session_is_not_reported_as_below_threshold(tmp_path):
    """The verdict's concrete miss: session_20260729-163518-1-94729.

    117 seconds of real work, non-empty transcript, and its retro.log reads
    ``skip: below threshold (turns=0<5, tool_calls=0<10)`` — a claim about
    numbers nobody measured. Skipping is fine; asserting the threshold is not.
    """
    metrics = tmp_path / METRICS_JSON
    metrics.write_text(json.dumps(UNMEASURED))

    action, reason = should_run(_config(tmp_path), metrics)

    assert action == "skip"
    assert "below threshold" not in reason
    assert "unmeasured" in reason


def test_measured_zero_still_reports_below_threshold(tmp_path):
    """A genuinely measured zero keeps the old, correct message."""
    metrics = tmp_path / METRICS_JSON
    metrics.write_text(json.dumps({**MEASURED, "turns": 0, "tool_calls": 0}))

    action, reason = should_run(_config(tmp_path), metrics)

    assert action == "skip"
    assert "below threshold" in reason


# ----------------------------------------------------------- retro frontmatter


def test_retro_facts_do_not_fabricate_a_clean_zero_record(tmp_path):
    """``_parse_metrics`` returned ``exit_code=0, turns=0, tool_calls=0`` for a
    session with no metrics.json at all — a clean-looking record for a session
    nobody measured, copied verbatim into retro frontmatter."""
    assert _parse_metrics(tmp_path).measured is False


def test_retro_facts_mark_a_real_parse_as_measured(tmp_path):
    (tmp_path / METRICS_JSON).write_text(json.dumps({**MEASURED, "turns": 7, "tool_calls": 12}))

    facts = _parse_metrics(tmp_path)

    assert facts.measured is True
    assert facts.turns == 7


# ------------------------------------- fixed-for-free by the writer change


def test_unmeasured_metrics_do_not_trigger_a_zero_output_incident():
    """``is_zero_output`` already returns False when the keys are absent, so
    honest absence removes the false-positive harness incident that fabricated
    zeros used to raise. Pinned: re-introducing the zeros would flip this."""
    assert is_zero_output(UNMEASURED) is False
    assert is_zero_output({**MEASURED, "tokens": {"output": 0}, "tool_calls": 0}) is True
    # The shape that actually shipped the bogus incident: unmeasured, yet still
    # carrying the old writer's zeros. Key-absence alone would let this through,
    # which is why the check gates on `measured` (found on a real artifact, not
    # by these synthetic dicts).
    assert (
        is_zero_output(
            {
                **UNMEASURED,
                "turns": 0,
                "tool_calls": 0,
                "tokens": {"input": 0, "output": 0},
            }
        )
        is False
    )


def test_enrichment_failure_is_recorded_in_the_artifact(tmp_path):
    """A dead sensor must leave a mark a retro can read, not just a log line.

    RC-C shipped 74 sessions whose metrics.json simply had no ``turns`` key,
    because the enrichment pipeline raised before its first step and the
    exception was swallowed into ``logger.warning``.
    """
    from ai_hats.runtime_common import _flag_sensor_error

    session_dir = tmp_path / "session_20260730-120000-1"
    session_dir.mkdir()
    (session_dir / METRICS_JSON).write_text(json.dumps({"exit_code": 0, "role": "maintainer"}))
    session = Session(session_id="20260730-120000-1", session_dir=session_dir)

    _flag_sensor_error(session)

    metrics = json.loads((session_dir / METRICS_JSON).read_text())
    assert metrics["flags"] == ["sensor-error"]
    assert metrics["measured"] is False
    assert is_measured(metrics) is False


def test_killed_session_leaves_a_readable_unfinalized_record(tmp_path):
    """RC-D: metrics.json opens at session start, not only at teardown.

    audit.md was already written by ``init_audit`` while metrics.json waited for
    the teardown ``finally`` — so a SIGKILLed session left 48 dirs on disk with
    an audit and no metrics, which every consumer read as "no session" rather
    than "not finalized".
    """
    session_dir = tmp_path / "session_20260730-120000-1"
    session_dir.mkdir()
    session = Session(session_id="20260730-120000-1", session_dir=session_dir)

    session.init_audit(role="maintainer", provider="agy", model="gemini-3-pro")
    # …killed here: no finalize_audit call.

    metrics = json.loads((session_dir / METRICS_JSON).read_text())
    assert metrics["finalized"] is False
    assert "not-finalized" in metrics["flags"]
    assert metrics["role"] == "maintainer"
    assert metrics["provider"] == "agy"
    assert is_measured(metrics) is False


def test_finalize_supersedes_the_startup_stub(tmp_path):
    session_dir = tmp_path / "session_20260730-120000-1"
    session_dir.mkdir()
    session = Session(session_id="20260730-120000-1", session_dir=session_dir)
    session.init_audit(role="maintainer", provider="claude")

    session.finalize_audit({"exit_code": 0, "role": "maintainer", "provider": "claude"})

    metrics = json.loads((session_dir / METRICS_JSON).read_text())
    assert metrics["finalized"] is True
    assert "not-finalized" not in metrics.get("flags", [])


def test_unmeasured_session_is_not_claimed_productive(tmp_path):
    """``is_productive`` must stay False — an unmeasured session is not provably
    productive — but for the honest reason (no counters), not a fabricated zero."""
    session_dir = tmp_path / "session_20260730-120000-1"
    session_dir.mkdir()
    (session_dir / METRICS_JSON).write_text(json.dumps(UNMEASURED))

    session = Session(session_id="20260730-120000-1", session_dir=session_dir)

    assert session.is_productive() is False
