"""Tests for ai_hats.harness (HATS-378 Phase 1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_hats.harness.diagnostic import diagnose_silent_session, is_zero_output
from ai_hats.harness.errors import HarnessReliabilityError, HarnessZeroOutputError
from ai_hats.harness.guard import apply_post_run_guard
from ai_hats.pipeline.harness_policy import HarnessPolicy
from ai_hats.paths import METRICS_JSON, REASONING_LOG


# ---- fake session ----


@dataclass
class FakeSession:
    """Minimal Session stub for guard testing."""

    session_id: str
    session_dir: Path

    @property
    def metrics_path(self) -> Path:
        return self.session_dir / METRICS_JSON


def _make_session(tmp_path: Path, metrics: dict | None = None, *, stderr: str = ""):
    session = FakeSession(session_id="test-1", session_dir=tmp_path)
    if metrics is not None:
        session.metrics_path.write_text(json.dumps(metrics))
    if stderr:
        (tmp_path / REASONING_LOG).write_text(stderr)
    return session


# ---- is_zero_output ----


def test_is_zero_output_true_when_both_zero():
    assert is_zero_output({"tokens": {"output": 0}, "tool_calls": 0}) is True


def test_is_zero_output_false_when_tokens_positive():
    assert is_zero_output({"tokens": {"output": 5}, "tool_calls": 0}) is False


def test_is_zero_output_false_when_tool_calls_positive():
    assert is_zero_output({"tokens": {"output": 0}, "tool_calls": 3}) is False


def test_is_zero_output_false_when_tokens_absent():
    # Sub-agent metrics (basic _finalize) have no tokens dict — guard must
    # not fire defensively (false positives are worse than misses).
    assert is_zero_output({"exit_code": 0, "tool_calls": 0}) is False


def test_is_zero_output_false_when_tool_calls_absent():
    assert is_zero_output({"tokens": {"output": 0}}) is False


def test_is_zero_output_false_when_tokens_not_a_dict():
    assert is_zero_output({"tokens": "n/a", "tool_calls": 0}) is False


# ---- diagnose_silent_session ----


def test_diagnose_includes_session_id(tmp_path: Path):
    session = _make_session(tmp_path)
    diag = diagnose_silent_session(session)
    assert "sub-session=test-1" in diag


def test_diagnose_surfaces_exit_code_and_timed_out(tmp_path: Path):
    session = _make_session(
        tmp_path, {"exit_code": 124, "timed_out": True, "role": "x"}
    )
    diag = diagnose_silent_session(session)
    assert "exit_code=124" in diag
    assert "timed_out=True" in diag


def test_diagnose_omits_falsy_optional_fields(tmp_path: Path):
    session = _make_session(
        tmp_path, {"exit_code": 0, "timed_out": False, "error": None}
    )
    diag = diagnose_silent_session(session)
    # exit_code=0 is informational; timed_out=False/error=None must be skipped
    assert "timed_out" not in diag
    assert "error" not in diag


def test_diagnose_includes_stderr_tail(tmp_path: Path):
    session = _make_session(
        tmp_path, {"exit_code": 1}, stderr="some\nfailure output here"
    )
    diag = diagnose_silent_session(session)
    assert "stderr_tail=" in diag
    assert "failure output here" in diag


def test_diagnose_handles_corrupt_metrics(tmp_path: Path):
    session = FakeSession(session_id="test-1", session_dir=tmp_path)
    session.metrics_path.write_text("{not valid json")
    diag = diagnose_silent_session(session)
    assert "metrics=unreadable" in diag


# ---- apply_post_run_guard ----


def test_guard_noop_when_policy_none(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 0}, "tool_calls": 0},
    )
    apply_post_run_guard(session, None)  # must not raise


def test_guard_noop_when_reporting_false(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 0}, "tool_calls": 0},
    )
    apply_post_run_guard(session, HarnessPolicy(reporting=False))


def test_guard_noop_when_on_zero_output_ignore(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 0}, "tool_calls": 0},
    )
    apply_post_run_guard(
        session, HarnessPolicy(reporting=True, on_zero_output="ignore"),
    )


def test_guard_raises_on_zero_output_with_reporting(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 0}, "tool_calls": 0},
    )
    with pytest.raises(HarnessZeroOutputError) as exc_info:
        apply_post_run_guard(
            session,
            HarnessPolicy(reporting=True, on_zero_output="harness_incident"),
        )
    assert exc_info.value.session_id == "test-1"
    assert "sub-session=test-1" in str(exc_info.value)


def test_guard_zero_output_error_is_reliability_error(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 0}, "tool_calls": 0},
    )
    with pytest.raises(HarnessReliabilityError):
        apply_post_run_guard(
            session, HarnessPolicy(reporting=True),
        )


def test_guard_noop_on_non_zero_exit(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 1, "tokens": {"output": 0}, "tool_calls": 0},
    )
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


def test_guard_noop_when_timed_out(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {
            "exit_code": 124, "timed_out": True,
            "tokens": {"output": 0}, "tool_calls": 0,
        },
    )
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


def test_guard_noop_when_output_nonzero(tmp_path: Path):
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "tokens": {"output": 42}, "tool_calls": 0},
    )
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


def test_guard_noop_when_metrics_missing(tmp_path: Path):
    session = FakeSession(session_id="test-1", session_dir=tmp_path)
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


def test_guard_noop_on_corrupt_metrics(tmp_path: Path):
    session = FakeSession(session_id="test-1", session_dir=tmp_path)
    session.metrics_path.write_text("{not json")
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


def test_guard_noop_for_sub_agent_basic_metrics(tmp_path: Path):
    """Sub-agent metrics from _finalize_sub_agent lack tokens/tool_calls.

    Guard must not fire (false positive guard against real session-reviewer
    runs that simply haven't been trace-enriched yet — HATS-271 transcript
    check is the safety net for that path).
    """
    session = _make_session(
        tmp_path,
        {"exit_code": 0, "role": "session-reviewer", "model": "claude-sonnet-4-6"},
    )
    apply_post_run_guard(session, HarnessPolicy(reporting=True))


# ---- error type sanity ----


def test_zero_output_error_carries_session_and_diagnostic():
    err = HarnessZeroOutputError("sid-1", "exit_code=0; turns=0")
    assert err.session_id == "sid-1"
    assert err.diagnostic == "exit_code=0; turns=0"
    assert "sid-1" in str(err)
    assert "exit_code=0" in str(err)
