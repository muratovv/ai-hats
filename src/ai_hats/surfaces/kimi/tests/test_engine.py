"""KimiSubagentEngine tests — a recorded NDJSON stream drives an injected
spawn seam; no real kimi, no auth."""

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


class _FakeProc:
    """The Popen handle the engine drives: pid, communicate, kill."""

    def __init__(self, stdout="", stderr="", returncode=0, *, hang=False):
        self.pid = 4242
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    def communicate(self, timeout=None):
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(cmd="kimi", timeout=timeout)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _engine(stdout=_STREAM, returncode=0, stderr="", *, hang=False):
    proc = _FakeProc(stdout, stderr, returncode, hang=hang)

    def fake_popen(*args, **kwargs):
        return proc

    return KimiSubagentEngine(popen=fake_popen), proc


def _run(engine, **overrides):
    metrics = _Metrics()
    kwargs = {
        "layout": SimpleNamespace(),
        "work_dir": Path("/wd"),
        "session_id": "sid",
        "env": {},
        "model": None,
        "timeout_s": 30,
        "metrics": metrics,
        "launched": SimpleNamespace(args=("kimi", "-p", "brief")),
        "brief": "brief",
        **overrides,
    }
    return engine.run(**kwargs), metrics


def test_completed_run_extracts_text_and_session_id():
    engine, _ = _engine()
    result, metrics = _run(engine)
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert result.error is None
    assert metrics.values["kimi_session_id"] == "session_abc123"
    assert metrics.values["provider_session_id"] == "session_abc123"
    assert metrics.values["num_turns"] == 2
    assert metrics.values["stop_reason"] == "completed"


def test_failed_run_carries_exit_code():
    engine, _ = _engine(returncode=1, stdout="")
    result, metrics = _run(engine)
    assert result.exit_code == 1
    assert result.error
    assert metrics.values["stop_reason"] == "error"
    assert metrics.values["kimi_session_id"] == ""


def test_spawn_reports_the_child_pid():
    """The cache sweep reaps on proof of death — a child that outlives its
    wrapper must be anchored, or a live session loses its gates mid-flight."""
    engine, proc = _engine()
    spawned = []
    _run(engine, on_spawn=spawned.append)
    assert spawned == [proc.pid]


def test_timeout_kills_and_reports():
    engine, proc = _engine(hang=True)
    result, _ = _run(engine)
    assert proc.killed is True
    assert result.timed_out is True
    assert result.exit_code == 124


def test_timeout_with_partial_output():
    """The partial stream still yields its assistant text — no crash, no loss."""
    engine, proc = _engine(hang=True)
    proc._stdout = '{"role":"assistant","content":"half"}\n'
    proc._stderr = "interrupted"
    result, _ = _run(engine)
    assert result.timed_out is True
    assert result.stdout == "half"
    assert result.stderr == "interrupted"


def test_timeout_recovers_the_session_id_from_the_partial_stream():
    """A timed-out run still wrote a wire.jsonl — recording the id keeps its
    audit on the exact transcript instead of the mtime guess."""
    engine, proc = _engine(hang=True)
    proc._stdout = _STREAM
    result, metrics = _run(engine)
    assert result.timed_out is True
    assert metrics.values["provider_session_id"] == "session_abc123"
    assert metrics.values["stop_reason"] == "timeout"


def test_malformed_stream_lines_are_skipped():
    noisy = 'not json\n{"role":"assistant","content":"ok"}\n{"broken\n'
    engine, _ = _engine(stdout=noisy)
    result, _ = _run(engine)
    assert result.stdout == "ok"


def test_spawn_oserror_is_exit_127():
    def fake_popen(*args, **kwargs):
        raise OSError("kimi: command not found")

    result, _ = _run(KimiSubagentEngine(popen=fake_popen))
    assert result.exit_code == 127
    assert "command not found" in result.error


def test_child_env_merges_additions_over_inherited(monkeypatch):
    """The runner hands only ai-hats' additions; PATH & co. come from the
    inherited environment — a wholesale replacement loses the binary itself."""
    seen = {}

    def fake_popen(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        return _FakeProc(_STREAM, "")

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    engine = KimiSubagentEngine(popen=fake_popen)
    _run(engine, env={"AI_HATS_SESSION_ID": "sid"})
    assert seen["AI_HATS_SESSION_ID"] == "sid"
    assert seen["PATH"] == "/usr/bin:/bin"
