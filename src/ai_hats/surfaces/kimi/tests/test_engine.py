"""KimiSubagentEngine tests — a recorded NDJSON stream drives an injected
runner; no real kimi, no auth."""

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


def _engine(stdout=_STREAM, returncode=0, stderr=""):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode, stdout, stderr)

    return KimiSubagentEngine(run=fake_run)


def _run(engine):
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


def test_completed_run_extracts_text_and_session_id():
    result, metrics = _run(_engine())
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert result.error is None
    assert metrics.values["kimi_session_id"] == "session_abc123"
    assert metrics.values["provider_session_id"] == "session_abc123"
    assert metrics.values["num_turns"] == 2
    assert metrics.values["stop_reason"] == "completed"


def test_failed_run_carries_exit_code():
    result, metrics = _run(_engine(returncode=1, stdout=""))
    assert result.exit_code == 1
    assert result.error
    assert metrics.values["stop_reason"] == "error"
    assert metrics.values["kimi_session_id"] == ""


def test_timeout_is_reported():
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="kimi", timeout=30)

    result, _ = _run(KimiSubagentEngine(run=fake_run))
    assert result.timed_out is True
    assert result.exit_code == 124


def test_timeout_with_partial_bytes_output():
    """TimeoutExpired.stdout is BYTES even under text=True on POSIX — the
    partial stream must still yield its assistant text, not crash the branch."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="kimi",
            timeout=30,
            output=b'{"role":"assistant","content":"half"}\n',
            stderr=b"interrupted",
        )

    result, _ = _run(KimiSubagentEngine(run=fake_run))
    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout == "half"
    assert result.stderr == "interrupted"


def test_malformed_stream_lines_are_skipped():
    noisy = 'not json\n{"role":"assistant","content":"ok"}\n{"broken\n'
    result, _ = _run(_engine(stdout=noisy))
    assert result.stdout == "ok"


def test_child_env_merges_additions_over_inherited(monkeypatch):
    """The runner hands only ai-hats' additions; PATH & co. come from the
    inherited environment — a wholesale replacement loses the binary itself."""
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(args[0], 0, _STREAM, "")

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    engine = KimiSubagentEngine(run=fake_run)
    metrics = _Metrics()
    engine.run(
        layout=SimpleNamespace(),
        work_dir=Path("/wd"),
        session_id="sid",
        env={"AI_HATS_SESSION_ID": "sid"},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=SimpleNamespace(args=("kimi", "-p", "brief")),
        brief="brief",
    )
    assert seen["AI_HATS_SESSION_ID"] == "sid"
    assert seen["PATH"] == "/usr/bin:/bin"
