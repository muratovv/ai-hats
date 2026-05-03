"""Tests for observe.py — SidecarTracer."""

from __future__ import annotations

import os

import pytest

from ai_hats.observe import Session, SidecarTracer


def make_test_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def pipe_with(data: bytes) -> int:
    """Create a pipe pre-loaded with data, return read fd (write end closed)."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return read_fd


# --- unit tests ---

def test_master_read_logs_res_and_returns_data(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"hello from CLI\n")

    result = tracer.make_master_read()(fd)

    assert result == b"hello from CLI\n"
    trace = session.trace_path.read_text()
    assert "[RES]" in trace
    assert "hello from CLI" in trace


def test_master_read_strips_ansi(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[32mgreen text\x1b[0m\n")

    tracer.make_master_read()(fd)

    trace = session.trace_path.read_text()
    assert "green text" in trace
    assert "\x1b" not in trace


def test_master_read_skips_empty_chunks(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[0m   \x1b[K\n")  # only escapes and whitespace

    tracer.make_master_read()(fd)

    assert not session.trace_path.exists()


def test_stdin_read_buffers_until_newline(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    stdin_read = tracer.make_stdin_read()

    fd1 = pipe_with(b"partial")
    stdin_read(fd1)
    assert not session.trace_path.exists()

    fd2 = pipe_with(b"\n")
    stdin_read(fd2)
    trace = session.trace_path.read_text()
    assert "[REQ]" in trace
    assert "partial" in trace


def test_stdin_read_returns_data_unchanged(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"hello\n")

    result = tracer.make_stdin_read()(fd)

    assert result == b"hello\n"


def test_stdin_read_skips_empty_input(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\n")  # newline only — nothing to log

    tracer.make_stdin_read()(fd)

    assert not session.trace_path.exists()


def test_stdin_read_strips_zellij_prefix(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b">|Zellij(4301)>|Zellij(4301)ffhello\n")

    tracer.make_stdin_read()(fd)

    trace = session.trace_path.read_text()
    assert "[REQ]" in trace
    assert "hello" in trace
    assert "Zellij" not in trace


# --- model response buffering ---

def test_master_read_captures_pio_response(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with("⏺Привет! Я твой ассистент.".encode())

    tracer.make_master_read()(fd)

    assert tracer._res_buf == ["Привет! Я твой ассистент."]


def test_master_read_last_pio_wins(tmp_path):
    """Tool calls (earlier ⏺) are replaced by final text response (later ⏺)."""
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    master_read = tracer.make_master_read()

    master_read(pipe_with("⏺Searching for 1 pattern…".encode()))
    master_read(pipe_with("⏺Вот ответ модели.".encode()))

    assert tracer._res_buf == ["Вот ответ модели."]


def test_flush_response_writes_bot_emoji(tmp_path):
    session = make_test_session(tmp_path)
    session.init_audit(role="test", provider="claude")
    tracer = SidecarTracer(session)
    tracer._res_buf = ["Привет! Я твой ассистент."]

    tracer.flush_response()

    assert "👾 Привет! Я твой ассистент." in session.audit_path.read_text()
    assert tracer._res_buf == []


def test_flush_response_noop_if_empty(tmp_path):
    session = make_test_session(tmp_path)
    session.init_audit(role="test", provider="claude")
    tracer = SidecarTracer(session)

    tracer.flush_response()  # should not raise, should not write

    assert "👾" not in session.audit_path.read_text()


def test_stdin_read_flushes_response_on_req(tmp_path):
    session = make_test_session(tmp_path)
    session.init_audit(role="test", provider="claude")
    tracer = SidecarTracer(session)
    tracer._res_buf = ["ответ модели"]

    fd = pipe_with("следующий вопрос\n".encode())
    tracer.make_stdin_read()(fd)

    assert "👾 ответ модели" in session.audit_path.read_text()


# --- integration test ---

@pytest.mark.integration
def test_wrap_runner_pty_spawn_writes_trace(tmp_path):
    """WrapRunner._pty_spawn uses SidecarTracer and writes [RES] to trace."""
    from ai_hats.runtime import WrapRunner

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="t", session_dir=session_dir)
    tracer = SidecarTracer(session)

    runner = object.__new__(WrapRunner)
    exit_code = runner._pty_spawn(["echo", "hello wrap"], {}, tracer)

    assert exit_code == 0
    trace = session.trace_path.read_text()
    assert "[RES]" in trace
    assert "hello wrap" in trace

