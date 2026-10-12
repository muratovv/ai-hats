"""KimiSubagentEngine tests — the NDJSON stream from the HATS-2059 spike drives
a stubbed subprocess; no real kimi, no auth."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from ai_hats.surfaces.kimi.provider import KimiSubagentEngine

_STREAM = (
    '{"role":"assistant","tool_calls":[{"type":"function","id":"t1",'
    '"function":{"name":"Bash","arguments":"{\\"command\\":\\"echo hi\\"}"}}]}\n'
    '{"role":"tool","tool_call_id":"t1","content":"hi\\n"}\n'
    '{"role":"assistant","content":"done"}\n'
    '{"role":"meta","type":"session.resume_hint","session_id":"session_abc123",'
    '"command":"kimi -r session_abc123","content":"..."}\n'
)


class _Metrics:
    def __init__(self):
        self.values: dict = {}

    def record(self, values) -> None:
        self.values.update(values)


def _run(engine, monkeypatch, *, returncode=0, stdout=_STREAM, stderr=""):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], returncode, stdout, stderr),
    )
    metrics = _Metrics()
    result = engine.run(
        layout=SimpleNamespace(),
        work_dir=Path("/wd"),
        session_id="sid",
        env={},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=SimpleNamespace(args=("kimi", "-p", "brief")),
        brief="brief",
    )
    return result, metrics


def test_completed_run_extracts_text_and_session_id(monkeypatch):
    result, metrics = _run(KimiSubagentEngine(), monkeypatch)
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert result.error is None
    assert metrics.values["kimi_session_id"] == "session_abc123"
    assert metrics.values["num_turns"] == 2
    assert metrics.values["stop_reason"] == "completed"


def test_failed_run_carries_exit_code(monkeypatch):
    result, metrics = _run(KimiSubagentEngine(), monkeypatch, returncode=1, stdout="")
    assert result.exit_code == 1
    assert result.error
    assert metrics.values["stop_reason"] == "error"
    assert metrics.values["kimi_session_id"] == ""


def test_timeout_is_reported(monkeypatch):
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="kimi", timeout=30)

    monkeypatch.setattr(subprocess, "run", _timeout)
    metrics = _Metrics()
    result = KimiSubagentEngine().run(
        layout=SimpleNamespace(),
        work_dir=Path("/wd"),
        session_id="sid",
        env={},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=SimpleNamespace(args=("kimi", "-p", "brief")),
        brief="brief",
    )
    assert result.timed_out is True
    assert result.exit_code == 124


def test_malformed_stream_lines_are_skipped(monkeypatch):
    noisy = 'not json\n{"role":"assistant","content":"ok"}\n{"broken\n'
    result, _ = _run(KimiSubagentEngine(), monkeypatch, stdout=noisy)
    assert result.stdout == "ok"
