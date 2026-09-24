"""The pre-launch half of an Automate session: what start-up found is recorded,
and a blocking readiness finding ends the run before anything is taken — the
session says why in ``events.jsonl`` and a finalized ``metrics.json``.

``preflight`` is the whole decision; ``SubAgentRunner._run_attempt`` returns the
session when it says ``True`` (the e2e proves the attempt never started)."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from ai_hats_core.diagnostics import Diagnostic, Level
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
from ai_hats.runtime_common import preflight
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


class _Recovered:
    def run(self):
        return (Diagnostic(Level.NOTE, "runs retention: dropped 2 files / 140 bytes"),)


def _refuse(tmp_path: Path, provider: _Provider) -> tuple[bool, SessionRun]:
    layout = ProjectLayout.at(tmp_path)
    manager = SessionManager(tmp_path, runs_dir=layout.sessions.runs, recovery=_Recovered())
    run = SessionRun.create(manager)
    with run:
        refused = preflight(
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


def test_a_ready_surface_is_not_refused_and_leaves_no_refusal(tmp_path):
    """Positive control: the same road, no finding → ``False``, no refusal written."""
    provider = _Provider(findings=[])

    refused, run = _refuse(tmp_path, provider)

    assert refused is False
    assert provider.asked_with == [{"PATH": "/usr/bin"}]
    assert not (run.session.session_dir / EVENT_LOG_JSONL).exists()
    assert not run.session.metrics_path.exists()


def _startup(run: SessionRun) -> dict:
    return json.loads((run.session.session_dir / "diagnostics.json").read_text())["startup"]


def test_a_refused_run_keeps_what_start_up_found(tmp_path):
    """No banner on this path: the record is the only place recovery's report
    and the probe's findings land — a refusal must not skip it."""
    _refused, run = _refuse(tmp_path, _Provider(findings=[_NOTICE, _REFUSAL]))

    assert _startup(run) == {
        "hold_seconds": 0.0,
        "notices": [
            {"level": "note", "text": "runs retention: dropped 2 files / 140 bytes"},
            {"level": "warn", "text": "probe unavailable"},
            {"level": "warn", "text": _REFUSAL.detail},
        ],
    }


def test_a_ready_run_keeps_what_start_up_found_too(tmp_path):
    _refused, run = _refuse(tmp_path, _Provider(findings=[]))

    assert _startup(run)["notices"] == [
        {"level": "note", "text": "runs retention: dropped 2 files / 140 bytes"}
    ]


def test_findings_go_to_the_trace_not_to_stderr(tmp_path, caplog):
    """The CLI configures no logging: a warning would reach stderr through
    ``logging.lastResort``, beside the line that already says it."""
    with caplog.at_level(logging.WARNING):
        _refused, run = _refuse(tmp_path, _Provider(findings=[_NOTICE, _REFUSAL]))

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    trace = run.session.trace_path.read_text()
    assert "probe unavailable" in trace
    assert _REFUSAL.detail in trace


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


@dataclass
class _SlowProbe(_Provider):
    """Stamps its finding when it answers, the way a real probe does."""

    def readiness_findings(self, environ):
        import time

        from ai_hats_observe.canonical.types import now

        time.sleep(0.005)
        return [dataclasses.replace(_REFUSAL, ts=now())]


def test_the_run_starts_before_the_probe_answers(tmp_path):
    """Seen live: ``run_started`` stamped at write time came after the signal
    the probe had stamped — a reader sorting by ``ts`` put the signal first."""
    _refused, run = _refuse(tmp_path, _SlowProbe())

    started, signal, ended = read_events(run.session.session_dir / EVENT_LOG_JSONL)
    assert started.ts < signal.ts <= ended.ts
