"""SubAgentRunner timeout retry orchestration (HATS-378 Phase 2 / HATS-321).

Tests focus on the retry loop in ``SubAgentRunner.run`` — orchestrates
``_run_attempt`` calls based on ``HarnessPolicy.on_timeout`` and final
escalation to :class:`HarnessTimeoutError` when retries are exhausted.

The inner ``_run_attempt`` is monkey-patched out to avoid subprocess
plumbing; full sub-agent execution paths are covered by
test_subagent_graceful_finalize.py + test_session_review_runner.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_hats.harness.errors import (
    HarnessReliabilityError,
    HarnessTimeoutError,
)
from ai_hats.pipeline.harness_policy import HarnessPolicy, TimeoutPolicy
from ai_hats.runtime import SUBAGENT_SUBPROCESS_TIMEOUT_S, SubAgentRunner


# ---- fake sessions ----


@dataclass
class _FakeSession:
    session_id: str
    session_dir: Path

    @property
    def metrics_path(self) -> Path:
        return self.session_dir / "metrics.json"


def _make_session(tmp_path: Path, sid: str, *, metrics: dict) -> _FakeSession:
    sdir = tmp_path / sid
    sdir.mkdir(exist_ok=True)
    (sdir / "metrics.json").write_text(json.dumps(metrics))
    return _FakeSession(session_id=sid, session_dir=sdir)


def _timed_out(tmp_path: Path, sid: str) -> _FakeSession:
    return _make_session(
        tmp_path, sid, metrics={"exit_code": 124, "timed_out": True, "role": "x"},
    )


def _success(tmp_path: Path, sid: str) -> _FakeSession:
    return _make_session(tmp_path, sid, metrics={"exit_code": 0, "role": "x"})


def _make_runner(monkeypatch, sessions, project_dir: Path) -> tuple[SubAgentRunner, list]:
    """Build a SubAgentRunner with ``_run_attempt`` stubbed to return sessions
    in order. Records each call's kwargs in the returned list.
    """
    calls: list[dict] = []
    iterator = iter(sessions)

    def fake_run_attempt(**kwargs):
        calls.append(kwargs)
        return next(iterator)

    runner = SubAgentRunner.__new__(SubAgentRunner)
    runner.project_dir = project_dir
    monkeypatch.setattr(runner, "_run_attempt", fake_run_attempt)
    return runner, calls


# ---- no policy: legacy behaviour preserved ----


def test_no_policy_no_retry(tmp_path: Path, monkeypatch) -> None:
    """Without ``harness_policy``, a timeout returns session as-is — pre-HATS-378
    behaviour (caller decides what to do with ``timed_out=True``)."""
    session = _timed_out(tmp_path, "s1")
    runner, calls = _make_runner(monkeypatch, [session], tmp_path)

    result = runner.run()

    assert len(calls) == 1
    assert result is session


def test_no_on_timeout_policy_no_retry(tmp_path: Path, monkeypatch) -> None:
    """Policy with reporting only (no on_timeout) — timeout still returns
    session, no retry, no HarnessTimeoutError."""
    session = _timed_out(tmp_path, "s1")
    runner, calls = _make_runner(monkeypatch, [session], tmp_path)

    result = runner.run(
        harness_policy=HarnessPolicy(reporting=True, on_timeout=None),
    )

    assert len(calls) == 1
    assert result is session


# ---- retry policy in effect ----


def test_retries_once_on_first_timeout_then_succeeds(
    tmp_path: Path, monkeypatch,
) -> None:
    sessions = [_timed_out(tmp_path, "s1"), _success(tmp_path, "s2")]
    runner, calls = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(
        on_timeout=TimeoutPolicy(retry=1, budget_multiplier=2.0),
    )

    result = runner.run(harness_policy=policy)

    assert len(calls) == 2
    assert calls[0]["timeout_s"] == SUBAGENT_SUBPROCESS_TIMEOUT_S
    assert calls[1]["timeout_s"] == int(SUBAGENT_SUBPROCESS_TIMEOUT_S * 2.0)
    # First attempt has no retry tag; second is tagged.
    assert "harness_retry_attempt" not in calls[0]["tags"]
    assert calls[1]["tags"]["harness_retry_attempt"] == "2"
    assert result.session_id == "s2"


def test_raises_timeout_error_after_exhausted_retries(
    tmp_path: Path, monkeypatch,
) -> None:
    sessions = [_timed_out(tmp_path, "s1"), _timed_out(tmp_path, "s2")]
    runner, _ = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=1))

    with pytest.raises(HarnessTimeoutError) as exc_info:
        runner.run(harness_policy=policy)
    assert exc_info.value.session_id == "s2"
    assert "sub-session=s2" in str(exc_info.value)


def test_timeout_error_is_harness_reliability(
    tmp_path: Path, monkeypatch,
) -> None:
    """HarnessTimeoutError must be a HarnessReliabilityError so Phase 3
    routing can match a single base class for harness-incident."""
    sessions = [_timed_out(tmp_path, "s1")]
    runner, _ = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=0))

    with pytest.raises(HarnessReliabilityError):
        runner.run(harness_policy=policy)


def test_retry_zero_means_one_attempt_then_escalate(
    tmp_path: Path, monkeypatch,
) -> None:
    """``retry=0`` policy — one attempt only, escalate immediately on timeout."""
    sessions = [_timed_out(tmp_path, "s1")]
    runner, calls = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=0))

    with pytest.raises(HarnessTimeoutError):
        runner.run(harness_policy=policy)
    assert len(calls) == 1


def test_success_on_first_attempt_skips_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    sessions = [_success(tmp_path, "s1")]
    runner, calls = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=2, budget_multiplier=3))

    result = runner.run(harness_policy=policy)

    assert len(calls) == 1
    assert result.session_id == "s1"


def test_budget_multiplier_applied_only_to_retries(
    tmp_path: Path, monkeypatch,
) -> None:
    """The first attempt always uses base budget; multiplier kicks in on retry."""
    sessions = [
        _timed_out(tmp_path, "s1"),
        _timed_out(tmp_path, "s2"),
        _success(tmp_path, "s3"),
    ]
    runner, calls = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=2, budget_multiplier=2.5))

    runner.run(harness_policy=policy)

    assert calls[0]["timeout_s"] == SUBAGENT_SUBPROCESS_TIMEOUT_S
    expected = int(SUBAGENT_SUBPROCESS_TIMEOUT_S * 2.5)
    assert calls[1]["timeout_s"] == expected
    assert calls[2]["timeout_s"] == expected


def test_tags_threaded_through_retries(
    tmp_path: Path, monkeypatch,
) -> None:
    """Caller-supplied tags survive into each attempt; retry tag is additive."""
    sessions = [_timed_out(tmp_path, "s1"), _success(tmp_path, "s2")]
    runner, calls = _make_runner(monkeypatch, sessions, tmp_path)
    policy = HarnessPolicy(on_timeout=TimeoutPolicy(retry=1))

    runner.run(tags={"trace": "abc"}, harness_policy=policy)

    assert calls[0]["tags"]["trace"] == "abc"
    assert calls[1]["tags"]["trace"] == "abc"
    assert calls[1]["tags"]["harness_retry_attempt"] == "2"
