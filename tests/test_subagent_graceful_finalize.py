"""Graceful finalize for sub-agent sessions (HATS-165).

Every terminal path of SubAgentRunner (success, TimeoutExpired, generic error)
must leave session_dir consistently closed: transcript.txt written when we have
stdout, reasoning.log when we have stderr, metrics.json with exit_code and
optional timed_out/error fields. Behavior is provider-agnostic by design — the
same helper is called regardless of whether claude or agy produced the
output.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import pytest

from ai_hats_core.layout import ProjectLayout
from ai_hats_observe import Session
from ai_hats import runtime_common
from ai_hats.runtime import (
    SUBAGENT_EXIT_ERROR,
    SUBAGENT_EXIT_TIMEOUT,
    _finalize_sub_agent,
)
from ai_hats_observe.artifacts import REASONING_LOG, TRANSCRIPT_TXT


def _make_session(tmp_path, *, provider: str = "claude") -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    session = Session(session_id="test", session_dir=session_dir)
    session.init_audit(role="primary", provider=provider, model="")
    return session


def _read_metrics(session: Session) -> dict:
    return json.loads(session.metrics_path.read_text())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_success_saves_outputs_and_exit_code(tmp_path):
    """Exit 0 with stdout+stderr → both files written, metrics has exit_code=0,
    no timed_out or error fields."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="sonnet",
        isolation_mode="discard",
        exit_code=0,
        stdout="final answer\n",
        stderr="log line\n",
    )

    assert (session.session_dir / TRANSCRIPT_TXT).read_text() == "final answer\n"
    assert (session.session_dir / REASONING_LOG).read_text() == "log line\n"
    m = _read_metrics(session)
    assert m["exit_code"] == 0
    assert m["role"] == "primary"
    assert m["model"] == "sonnet"
    assert m["isolation_mode"] == "discard"
    assert "timed_out" not in m
    assert "error" not in m


def test_success_no_stderr_skips_reasoning_log(tmp_path):
    """Empty stderr → reasoning.log not created (avoid noise)."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok\n",
        stderr="",
    )

    assert (session.session_dir / TRANSCRIPT_TXT).exists()
    assert not (session.session_dir / REASONING_LOG).exists()


# ---------------------------------------------------------------------------
# TimeoutExpired path
# ---------------------------------------------------------------------------


def test_timeout_with_partial_output_saved(tmp_path):
    """Subprocess timed out mid-stream → partial stdout/stderr still written,
    exit_code=124, timed_out=True flagged in metrics."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="diagnoser",
        model="sonnet",
        isolation_mode="branch",
        exit_code=SUBAGENT_EXIT_TIMEOUT,
        stdout="partial transcript before kill\n",
        stderr="some reasoning before timeout\n",
        timed_out=True,
    )

    assert (session.session_dir / TRANSCRIPT_TXT).read_text() == "partial transcript before kill\n"
    assert (session.session_dir / REASONING_LOG).read_text() == "some reasoning before timeout\n"
    m = _read_metrics(session)
    assert m["exit_code"] == SUBAGENT_EXIT_TIMEOUT == 124
    assert m["timed_out"] is True
    assert m["isolation_mode"] == "branch"
    assert "error" not in m


def test_timeout_with_no_output_still_finalizes(tmp_path):
    """Subprocess hung before any output → no transcript/reasoning files, but
    metrics still written with exit_code=124 and timed_out=True. session_dir
    is never left half-initialized."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=SUBAGENT_EXIT_TIMEOUT,
        stdout="",
        stderr="",
        timed_out=True,
    )

    assert not (session.session_dir / TRANSCRIPT_TXT).exists()
    assert not (session.session_dir / REASONING_LOG).exists()
    m = _read_metrics(session)
    assert m["exit_code"] == 124
    assert m["timed_out"] is True


# ---------------------------------------------------------------------------
# Generic error path
# ---------------------------------------------------------------------------


def test_generic_error_records_exit_code_and_message(tmp_path):
    """Non-timeout exception (e.g. FileNotFoundError on CLI binary) →
    exit_code=1, error string captured. No partial output expected."""
    session = _make_session(tmp_path)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=SUBAGENT_EXIT_ERROR,
        error="FileNotFoundError: 'claude' not on PATH",
    )

    m = _read_metrics(session)
    assert m["exit_code"] == SUBAGENT_EXIT_ERROR == 1
    assert m["error"] == "FileNotFoundError: 'claude' not on PATH"
    assert "timed_out" not in m


# ---------------------------------------------------------------------------
# Cross-provider consistency — core ask of HATS-165
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["claude", "agy"])
def test_timeout_finalize_is_provider_agnostic(tmp_path, provider):
    """Finalize produces identical metrics.json structure regardless of which
    provider populated init_audit. This is what 'limits прокидываются одинаково'
    reduces to in the narrowed HATS-165 scope: graceful shutdown is uniform."""
    session = _make_session(tmp_path, provider=provider)

    _finalize_sub_agent(
        session,
        role="primary",
        provider=provider,
        model="",
        isolation_mode="discard",
        exit_code=SUBAGENT_EXIT_TIMEOUT,
        stdout="partial\n",
        stderr="",
        timed_out=True,
    )

    m = _read_metrics(session)
    # HATS-561: ``provider`` lands in metrics so post-session audit
    # rebuild stops emitting ``Surface: unknown``.
    assert m == {
        "schema_version": "audit/v1",  # HATS-948: versioned metrics surface
        "finalized": True,  # HATS-1374: teardown reached its end
        "exit_code": 124,
        "role": "primary",
        "provider": provider,
        "model": "",
        "isolation_mode": "discard",
        "timed_out": True,
    }
    # Transcript saved identically.
    assert (session.session_dir / TRANSCRIPT_TXT).read_text() == "partial\n"


@pytest.mark.parametrize("provider", ["claude", "agy"])
def test_success_finalize_is_provider_agnostic(tmp_path, provider):
    """Happy-path metrics structure is also provider-agnostic — guards against
    accidental provider-specific branching sneaking into the helper."""
    session = _make_session(tmp_path, provider=provider)

    _finalize_sub_agent(
        session,
        role="primary",
        provider=provider,
        model="sonnet",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok\n",
        stderr="",
    )

    m = _read_metrics(session)
    # HATS-561: ``provider`` lands in metrics so post-session audit
    # rebuild stops emitting ``Surface: unknown``.
    assert m == {
        "schema_version": "audit/v1",  # HATS-948: versioned metrics surface
        "finalized": True,  # HATS-1374: teardown reached its end
        "exit_code": 0,
        "role": "primary",
        "provider": provider,
        "model": "sonnet",
        "isolation_mode": "discard",
    }


# ---------------------------------------------------------------------------
# SIGINT shield parity with the HITL arm (HATS-1426)
# ---------------------------------------------------------------------------


def _sigint_sender(times: int):
    def send(*args, **kwargs):
        for _ in range(times):
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.02)

    return send


def _finalize_with_interrupt(session, tmp_path, monkeypatch, presses: int):
    """Run the sub-agent finalize with `presses` real SIGINTs mid-body.

    The signal is delivered from ``save_session_diagnostics`` — mid-body, so
    whether the trailing pipeline step still runs is the whole question.
    """
    reached: list[str] = []
    monkeypatch.setattr(runtime_common, "save_session_diagnostics", _sigint_sender(presses))
    monkeypatch.setattr(
        runtime_common,
        "_run_finalize_subagent",
        lambda *a, **k: reached.append("pipeline"),
    )

    _finalize_sub_agent(
        session,
        role="primary",
        provider="claude",
        model="sonnet",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok\n",
        stderr="",
        layout=ProjectLayout.at(tmp_path),
        extra_metrics={"claude_session_id": "sid"},
    )
    return reached


def test_stray_ctrl_c_does_not_cut_the_subagent_finalize(tmp_path, monkeypatch):
    # GIVEN a sub-agent finalize interrupted by one stray press
    session = _make_session(tmp_path)

    reached = _finalize_with_interrupt(session, tmp_path, monkeypatch, presses=1)

    # THEN the steps after the interrupt still ran
    assert reached == ["pipeline"]
    assert _read_metrics(session)["exit_code"] == 0


def test_third_ctrl_c_aborts_the_subagent_finalize(tmp_path, monkeypatch, caplog):
    # GIVEN the operator insisting three times inside the window
    session = _make_session(tmp_path)

    with caplog.at_level(logging.WARNING):
        reached = _finalize_with_interrupt(session, tmp_path, monkeypatch, presses=3)

    # THEN the remaining work is abandoned, on the record, without escaping
    assert reached == []
    assert "sub-agent finalize aborted by operator" in caplog.text


# ---------------------------------------------------------------------------
# What blocked the run
# ---------------------------------------------------------------------------


def _log_events(session: Session, *events) -> None:
    from ai_hats_observe.artifacts import EVENT_LOG_JSONL
    from ai_hats_observe.event_log import write_events

    write_events(events, session.session_dir / EVENT_LOG_JSONL)


def _refused_by_org(detail: str = "ask your admin"):
    from ai_hats_observe.canonical.signals import PersonActionRequired, PersonMustAct

    return PersonActionRequired(
        reason=PersonMustAct.AUTHORIZE, detail=detail, raw_code="403", source="claude/jsonl"
    )


def _finalize(session: Session, *, exit_code: int, error: str | None = None) -> dict:
    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=exit_code,
        error=error,
    )
    return _read_metrics(session)


def test_a_failed_run_says_what_blocked_it(tmp_path):
    from ai_hats_observe.canonical.events import RunEnded, RunStarted

    session = _make_session(tmp_path)
    _log_events(session, RunStarted(), _refused_by_org(), RunEnded(ok=False, raw_code="1"))

    m = _finalize(session, exit_code=SUBAGENT_EXIT_ERROR)

    blocked = m["blocked"]
    assert blocked["obligation"] == "person_must_act"
    assert blocked["kind"] == "authorize"
    assert blocked["detail"] == "ask your admin"
    assert blocked["raw_code"] == "403"
    assert blocked["source"] == "claude/jsonl"
    assert m["error"] == "a person must authorize: ask your admin"


def test_a_run_that_succeeded_is_not_blocked_by_a_signal_it_survived(tmp_path):
    # A Task sub-agent's own refusal lands in the same log; the main agent went on.
    session = _make_session(tmp_path)
    _log_events(session, _refused_by_org())

    m = _finalize(session, exit_code=0)

    assert "blocked" not in m
    assert "error" not in m


def test_a_failure_with_no_signal_is_not_blocked(tmp_path):
    from ai_hats_observe.canonical.events import RunEnded, RunStarted

    session = _make_session(tmp_path)
    _log_events(session, RunStarted(), RunEnded(ok=False, raw_code="1"))

    m = _finalize(session, exit_code=SUBAGENT_EXIT_ERROR)

    assert "blocked" not in m
    assert "error" not in m


def test_a_surface_that_writes_no_log_is_not_blocked(tmp_path):
    session = _make_session(tmp_path)

    m = _finalize(session, exit_code=SUBAGENT_EXIT_ERROR)

    assert "blocked" not in m


def test_the_runtime_error_is_kept_beside_what_blocked_the_run(tmp_path):
    session = _make_session(tmp_path)
    _log_events(session, _refused_by_org())

    m = _finalize(session, exit_code=SUBAGENT_EXIT_ERROR, error="ProcessError: exit 1")

    assert m["error"] == "ProcessError: exit 1"
    assert m["blocked"]["kind"] == "authorize"


def test_an_unreadable_log_costs_the_blocked_field_not_the_metrics(tmp_path, caplog):
    from ai_hats_observe.artifacts import EVENT_LOG_JSONL

    session = _make_session(tmp_path)
    (session.session_dir / EVENT_LOG_JSONL).mkdir()  # read_bytes() raises IsADirectoryError

    with caplog.at_level(logging.WARNING):
        m = _finalize(session, exit_code=SUBAGENT_EXIT_ERROR)

    assert m["exit_code"] == SUBAGENT_EXIT_ERROR
    assert "blocked" not in m
    assert "events.jsonl unreadable" in caplog.text
