"""Tests for LLMCaller implementations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ai_hats.retro.llm_caller import (
    LLMCaller,
    SubAgentLLMCaller,
    SubprocessLLMCaller,
)


class _FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_project(tmp_path: Path, *, provider: str = "claude") -> Path:
    (tmp_path / "ai-hats.yaml").write_text(
        f"provider: {provider}\nschema_version: 1\n"
    )
    return tmp_path


def test_subprocess_caller_invokes_provider_cli(monkeypatch, tmp_path: Path) -> None:
    project = _make_project(tmp_path, provider="claude")
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(stdout="hello world\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    caller = SubprocessLLMCaller(project, timeout=42)
    result = caller("say hi")

    assert result == "hello world\n"
    assert captured["cmd"] == ["claude", "--print", "-p", "say hi"]
    assert captured["kwargs"]["timeout"] == 42
    assert captured["kwargs"]["cwd"] == str(project)


def test_subprocess_caller_uses_provider_from_config(monkeypatch, tmp_path: Path) -> None:
    project = _make_project(tmp_path, provider="gemini")
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)
    SubprocessLLMCaller(project)("x")
    assert captured["cmd"] == ["gemini", "-p", "x"]


def test_subprocess_caller_raises_on_nonzero(monkeypatch, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _FakeProc(stdout="", stderr="boom", returncode=1),
    )
    with pytest.raises(RuntimeError, match="exited 1"):
        SubprocessLLMCaller(project)("x")


def test_subprocess_caller_raises_on_missing_binary(monkeypatch, tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    def fake_run(*a, **kw):
        raise FileNotFoundError("no claude")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="not found"):
        SubprocessLLMCaller(project)("x")


def test_subprocess_caller_raises_on_timeout(monkeypatch, tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        SubprocessLLMCaller(project, timeout=1)("x")


# --- SubAgentLLMCaller ---


class _FakeSession:
    def __init__(self, transcript: str, session_dir: Path) -> None:
        self.session_dir = session_dir
        (session_dir / "transcript.txt").write_text(transcript)


class _FakeRunner:
    def __init__(self, transcript: str, session_dir: Path) -> None:
        self.transcript = transcript
        self.session_dir = session_dir
        self.calls: list[dict] = []

    def run(self, role_name: str, task: str, **kw) -> _FakeSession:
        self.calls.append({"role_name": role_name, "task": task, **kw})
        return _FakeSession(self.transcript, self.session_dir)


def test_subagent_caller_returns_transcript(tmp_path: Path) -> None:
    sdir = tmp_path / "session"
    sdir.mkdir()
    runner = _FakeRunner(transcript="agent reply", session_dir=sdir)
    caller = SubAgentLLMCaller(tmp_path, runner=runner)
    result = caller("hello")
    assert result == "agent reply"
    assert runner.calls == [
        {"role_name": "assistant", "task": "hello", "isolation_mode": "discard"}
    ]


def test_subagent_caller_uses_custom_role(tmp_path: Path) -> None:
    sdir = tmp_path / "session"
    sdir.mkdir()
    runner = _FakeRunner(transcript="x", session_dir=sdir)
    SubAgentLLMCaller(tmp_path, role_name="judge", runner=runner)("p")
    assert runner.calls[0]["role_name"] == "judge"


# --- Protocol conformance ---


def test_both_callers_satisfy_protocol(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    sub: LLMCaller = SubprocessLLMCaller(project)
    sa: LLMCaller = SubAgentLLMCaller(tmp_path)
    assert callable(sub)
    assert callable(sa)


# --- Regression: HATS-131 / audit #12 ---


def test_llm_caller_does_not_import_anthropic() -> None:
    """Regression: --mode llm uses subprocess CLI, not the Anthropic SDK.

    Importing the llm_caller module must not pull `anthropic` into the
    interpreter — `anthropic` is in the optional `[costs]` extra and a
    silent hard dependency would break installs without that extra.

    Run in a fresh subprocess so prior tests' import side-effects don't
    pollute the assertion.
    """
    code = (
        "import sys\n"
        "import ai_hats.retro.llm_caller  # noqa: F401\n"
        "assert 'anthropic' not in sys.modules, "
        "'retro.llm_caller must not import anthropic SDK'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
