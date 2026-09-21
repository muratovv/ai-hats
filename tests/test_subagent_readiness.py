"""The readiness probe on the Automate path: a blocking finding ends the run
after the session is minted and before anything is taken, and the session
says why — ``events.jsonl`` and a finalized ``metrics.json``.

``refuse_unready`` is the whole decision; ``SubAgentRunner._run_attempt``
returns the session when it says ``True`` (the e2e proves no worktree and no
cache are taken on that road)."""

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

from ai_hats.runtime import SUBAGENT_EXIT_ERROR
from ai_hats.runtime_common import refuse_unready
from ai_hats.session_run import SessionRun

_REFUSAL = PersonActionRequired(
    reason=PersonMustAct.REAUTHENTICATE, detail="not logged in, run `x auth login`", source="t"
)
_NOTICE = Notice(reason=WorthRecording.SURFACE_WARNING, detail="probe unavailable", source="t")


@dataclass
class _Provider:
    name: str = "stub"
    findings: list = field(default_factory=list)
    raises: bool = False
    asked_with: list = field(default_factory=list)

    def readiness_findings(self, environ):
        self.asked_with.append(environ)
        if self.raises:
            raise RuntimeError("probe exploded")
        return list(self.findings)


def _refuse(tmp_path: Path, provider: _Provider) -> tuple[bool, SessionRun]:
    layout = ProjectLayout.at(tmp_path)
    run = SessionRun.create(SessionManager(tmp_path, runs_dir=layout.sessions.runs))
    with run:
        refused = refuse_unready(
            run,
            provider,
            {"PATH": "/usr/bin"},
            role="assistant",
            model="",
            isolation_mode="discard",
            tags={"k": "v"},
        )
    return refused, run


def test_a_blocking_finding_refuses_and_finalizes_the_session(tmp_path):
    refused, run = _refuse(tmp_path, _Provider(findings=[_REFUSAL]))

    assert refused is True
    metrics = json.loads(run.session.metrics_path.read_text())
    assert metrics["finalized"] is True
    assert metrics["exit_code"] == SUBAGENT_EXIT_ERROR
    assert metrics["error"] == _REFUSAL.detail
    assert metrics["provider"] == "stub"
    assert metrics["tags"] == {"k": "v"}
    assert "refused before launch" in run.session.trace_path.read_text()


def test_the_refusal_is_the_whole_event_log(tmp_path):
    _refused, run = _refuse(tmp_path, _Provider(findings=[_REFUSAL]))

    events = list(read_events(run.session.session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, PersonActionRequired, RunEnded]
    assert events[1].reason is PersonMustAct.REAUTHENTICATE
    assert events[1].detail == _REFUSAL.detail
    assert events[1].ts is not None
    assert (events[2].ok, events[2].raw_code, events[2].detail) == (
        False,
        str(SUBAGENT_EXIT_ERROR),
        _REFUSAL.detail,
    )


def test_a_ready_surface_is_not_refused_and_leaves_no_record(tmp_path):
    """Positive control: the same road, no finding → ``False``, nothing written."""
    provider = _Provider(findings=[])

    refused, run = _refuse(tmp_path, provider)

    assert refused is False
    assert provider.asked_with == [{"PATH": "/usr/bin"}]
    assert not (run.session.session_dir / EVENT_LOG_JSONL).exists()
    assert not run.session.metrics_path.exists()


def test_a_notice_is_logged_and_does_not_refuse(tmp_path):
    refused, run = _refuse(tmp_path, _Provider(findings=[_NOTICE]))

    assert refused is False
    assert "probe unavailable" in run.session.trace_path.read_text()


def test_a_probe_that_raises_is_reported_and_does_not_refuse(tmp_path):
    refused, run = _refuse(tmp_path, _Provider(raises=True))

    assert refused is False
    assert "probe exploded" in run.session.trace_path.read_text()


@pytest.mark.parametrize("findings", [[_NOTICE, _REFUSAL], [_REFUSAL, _NOTICE]])
def test_a_notice_beside_a_blocking_finding_still_refuses(tmp_path, findings):
    refused, run = _refuse(tmp_path, _Provider(findings=findings))

    assert refused is True
    events = list(read_events(run.session.session_dir / EVENT_LOG_JSONL))
    assert [type(e) for e in events] == [RunStarted, PersonActionRequired, RunEnded]
