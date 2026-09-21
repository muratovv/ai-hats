"""The readiness probe on the Automate path: a blocking finding ends the run
after the session is minted and before anything is taken, and the session
says why — ``events.jsonl`` and a finalized ``metrics.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout
from ai_hats_observe import SessionManager
from ai_hats_observe.artifacts import EVENT_LOG_JSONL
from ai_hats_observe.canonical.events import RunEnded, RunStarted
from ai_hats_observe.canonical.signals import (
    Notice,
    PersonActionRequired,
    PersonMustAct,
    WorthRecording,
)
from ai_hats_observe.event_log import read_events

from ai_hats.runtime import SUBAGENT_EXIT_ERROR, SubAgentRunner

_REFUSAL = PersonActionRequired(
    reason=PersonMustAct.REAUTHENTICATE, detail="not logged in, run `x auth login`", source="t"
)
_NOTICE = Notice(reason=WorthRecording.SURFACE_WARNING, detail="probe unavailable", source="t")


@dataclass
class _Provider:
    name: str = "stub"
    findings: list = field(default_factory=list)
    raises: bool = False

    def readiness_findings(self, environ):
        if self.raises:
            raise RuntimeError("probe exploded")
        return list(self.findings)


@dataclass
class _Payload:
    provider: _Provider
    effective_role: str = "assistant"


def _runner(tmp_path: Path, provider: _Provider, monkeypatch) -> SubAgentRunner:
    runner = SubAgentRunner.__new__(SubAgentRunner)
    runner.layout = ProjectLayout.at(tmp_path)
    runner.project_dir = tmp_path
    runner.payload = _Payload(provider)
    runner.session_mgr = SessionManager(tmp_path, runs_dir=tmp_path / "runs")
    launched: list[str] = []

    def _launch(run, **kwargs):
        launched.append(run.session.session_id)
        run.session.init_audit(role="assistant", provider=provider.name, model="")
        run.session.finalize_audit({"exit_code": 0, "role": "assistant"})
        return run.session, tmp_path

    monkeypatch.setattr(runner, "_run_session_attempt", _launch)
    runner.launched = launched  # type: ignore[attr-defined]
    return runner


def _attempt(runner: SubAgentRunner):
    return runner._run_attempt(
        task="ping",
        ticket_id="",
        model="",
        parent_session=None,
        isolation_mode="discard",
        tags={"k": "v"},
        system_prompt_override=None,
        timeout_s=5,
    )


def test_a_blocking_finding_refuses_before_the_session_attempt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, _Provider(findings=[_REFUSAL]), monkeypatch)

    session = _attempt(runner)

    assert runner.launched == [], "the session attempt ran — resources were taken"
    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["finalized"] is True
    assert metrics["exit_code"] == SUBAGENT_EXIT_ERROR
    assert metrics["error"] == _REFUSAL.detail
    assert metrics["provider"] == "stub"
    assert metrics["tags"] == {"k": "v"}


def test_the_refusal_is_the_whole_event_log(tmp_path, monkeypatch):
    runner = _runner(tmp_path, _Provider(findings=[_REFUSAL]), monkeypatch)

    session = _attempt(runner)

    events = list(read_events(session.session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, PersonActionRequired, RunEnded]
    assert events[1].reason is PersonMustAct.REAUTHENTICATE
    assert events[1].detail == _REFUSAL.detail
    assert events[1].ts is not None
    assert (events[2].ok, events[2].raw_code, events[2].detail) == (
        False,
        str(SUBAGENT_EXIT_ERROR),
        _REFUSAL.detail,
    )


def test_a_ready_surface_runs_the_session_attempt(tmp_path, monkeypatch):
    """Positive control for the refusal: the same runner, no finding → launched."""
    runner = _runner(tmp_path, _Provider(findings=[]), monkeypatch)

    session = _attempt(runner)

    assert runner.launched == [session.session_id]
    assert not (session.session_dir / EVENT_LOG_JSONL).exists()


def test_a_notice_is_logged_and_the_run_proceeds(tmp_path, monkeypatch):
    runner = _runner(tmp_path, _Provider(findings=[_NOTICE]), monkeypatch)

    session = _attempt(runner)

    assert runner.launched == [session.session_id]
    assert "probe unavailable" in session.trace_path.read_text()


def test_a_probe_that_raises_is_reported_and_the_run_proceeds(tmp_path, monkeypatch):
    runner = _runner(tmp_path, _Provider(raises=True), monkeypatch)

    session = _attempt(runner)

    assert runner.launched == [session.session_id]
    assert "probe exploded" in session.trace_path.read_text()


@pytest.mark.parametrize("findings", [[_NOTICE, _REFUSAL], [_REFUSAL, _NOTICE]])
def test_a_notice_beside_a_blocking_finding_still_refuses(tmp_path, monkeypatch, findings):
    runner = _runner(tmp_path, _Provider(findings=findings), monkeypatch)

    session = _attempt(runner)

    assert runner.launched == []
    events = list(read_events(session.session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, PersonActionRequired, RunEnded]
