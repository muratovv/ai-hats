"""Graceful finalize for sub-agent sessions (HATS-165).

Every terminal path of SubAgentRunner (success, TimeoutExpired, generic error)
must leave session_dir consistently closed: transcript.txt written when we have
stdout, reasoning.log when we have stderr, metrics.json with exit_code and
optional timed_out/error fields. Behavior is provider-agnostic by design — the
same helper is called regardless of whether claude or gemini produced the
output.
"""

from __future__ import annotations

import json

import pytest

from ai_hats.observe import Session
from ai_hats.runtime import (
    SUBAGENT_EXIT_ERROR,
    SUBAGENT_EXIT_TIMEOUT,
    _finalize_sub_agent,
)


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

    assert (session.session_dir / "transcript.txt").read_text() == "final answer\n"
    assert (session.session_dir / "reasoning.log").read_text() == "log line\n"
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

    assert (session.session_dir / "transcript.txt").exists()
    assert not (session.session_dir / "reasoning.log").exists()


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

    assert (session.session_dir / "transcript.txt").read_text() == "partial transcript before kill\n"
    assert (session.session_dir / "reasoning.log").read_text() == "some reasoning before timeout\n"
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

    assert not (session.session_dir / "transcript.txt").exists()
    assert not (session.session_dir / "reasoning.log").exists()
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


@pytest.mark.parametrize("provider", ["claude", "gemini"])
def test_timeout_finalize_is_provider_agnostic(tmp_path, provider):
    """Finalize produces identical metrics.json structure regardless of which
    provider populated init_audit. This is what 'limits прокидываются одинаково'
    reduces to in the narrowed HATS-165 scope: graceful shutdown is uniform."""
    session = _make_session(tmp_path, provider=provider)

    _finalize_sub_agent(
        session,
        role="primary",
        model="",
        isolation_mode="discard",
        exit_code=SUBAGENT_EXIT_TIMEOUT,
        stdout="partial\n",
        stderr="",
        timed_out=True,
    )

    m = _read_metrics(session)
    assert m == {
        "exit_code": 124,
        "role": "primary",
        "model": "",
        "isolation_mode": "discard",
        "timed_out": True,
    }
    # Transcript saved identically.
    assert (session.session_dir / "transcript.txt").read_text() == "partial\n"


@pytest.mark.parametrize("provider", ["claude", "gemini"])
def test_success_finalize_is_provider_agnostic(tmp_path, provider):
    """Happy-path metrics structure is also provider-agnostic — guards against
    accidental provider-specific branching sneaking into the helper."""
    session = _make_session(tmp_path, provider=provider)

    _finalize_sub_agent(
        session,
        role="primary",
        model="sonnet",
        isolation_mode="discard",
        exit_code=0,
        stdout="ok\n",
        stderr="",
    )

    m = _read_metrics(session)
    assert m == {
        "exit_code": 0,
        "role": "primary",
        "model": "sonnet",
        "isolation_mode": "discard",
    }
