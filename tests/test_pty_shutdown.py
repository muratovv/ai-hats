"""Unit tests for ai_hats.pty_shutdown (HATS-411).

Strategy
--------
We never spawn a real PTY here — that's the e2e layer's job (see
``tests/e2e/test_pty_shutdown_bounded.py``). Instead we substitute a
``FakeProc`` whose ``isalive`` is driven by a scripted sequence, and
monkeypatch ``os.killpg`` / ``os.waitpid`` to record signal escalation.

This isolates the *escalation contract* (grace → SIGTERM-pgroup →
SIGKILL → WNOHANG reap) from any kernel timing fragility.
"""

from __future__ import annotations

import os
import signal
import time

from ai_hats import pty_shutdown
from ai_hats.pty_shutdown import (
    _env_float,
    bounded_proc_shutdown,
    emit_terminal_reset,
)


class FakeProc:
    """Minimal duck-type substitute for ptyprocess.PtyProcess.

    *alive_sequence* is consumed by isalive() — once exhausted the
    process is treated as dead. *terminate_kills* controls whether
    ``terminate(force=True)`` ends the simulated child.
    """

    def __init__(
        self,
        *,
        pid: int = 99999,
        alive_sequence: list[bool] | None = None,
        terminate_kills: bool = True,
    ) -> None:
        self.pid = pid
        self.exitstatus: int | None = None
        self.signalstatus: int | None = None
        self._alive = list(alive_sequence or [True])
        self._terminate_kills = terminate_kills
        self.terminate_called = False
        self.terminate_force: bool | None = None

    def isalive(self) -> bool:
        if not self._alive:
            return False
        return self._alive.pop(0)

    def terminate(self, force: bool = False) -> None:
        self.terminate_called = True
        self.terminate_force = force
        if self._terminate_kills:
            self._alive = []


# ---------- _env_float ---------------------------------------------------


def test_env_float_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("AI_HATS_TEST_VAR", raising=False)
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


def test_env_float_parses_valid(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "1.25")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 1.25


def test_env_float_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "not-a-number")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


def test_env_float_falls_back_on_negative(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "-1.0")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


def test_env_float_falls_back_on_zero(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "0")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


def test_env_float_falls_back_on_empty(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


def test_env_float_falls_back_on_inf(monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_VAR", "inf")
    assert _env_float("AI_HATS_TEST_VAR", 5.0) == 5.0


# ---------- bounded_proc_shutdown — escalation contract -----------------


def _stub_signal_calls(monkeypatch) -> dict:
    """Record killpg / waitpid calls without touching the real OS."""
    calls: dict = {"killpg": [], "waitpid": []}

    def fake_getpgid(pid: int) -> int:
        return pid  # pretend pgroup == pid

    def fake_killpg(pgid: int, sig: int) -> None:
        calls["killpg"].append((pgid, sig))

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        calls["waitpid"].append((pid, flags))
        # Pretend the child has been reaped with exit status 0.
        return (pid, 0)

    monkeypatch.setattr(pty_shutdown.os, "getpgid", fake_getpgid)
    monkeypatch.setattr(pty_shutdown.os, "killpg", fake_killpg)
    monkeypatch.setattr(pty_shutdown.os, "waitpid", fake_waitpid)
    return calls


def test_child_exits_before_grace_no_signals(monkeypatch):
    """Happy path: child is already dead → no SIGTERM, no SIGKILL."""
    calls = _stub_signal_calls(monkeypatch)
    proc = FakeProc(alive_sequence=[False])  # dead on first poll

    t0 = time.monotonic()
    bounded_proc_shutdown(proc, grace_s=1.0, term_s=1.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5, f"early-exit path took {elapsed:.3f}s"
    assert calls["killpg"] == []
    assert not proc.terminate_called
    # WNOHANG reap is still attempted — that's fine, it's non-blocking.
    assert len(calls["waitpid"]) <= 1


def test_child_survives_grace_triggers_sigterm(monkeypatch):
    """Child alive past grace → SIGTERM to pgroup, then it dies in term window."""
    calls = _stub_signal_calls(monkeypatch)
    # Alive forever during grace, then dies once we re-poll after killpg.
    # _POLL_INTERVAL_S = 0.05, grace_s=0.2 → ~4 isalive() calls.
    proc = FakeProc(alive_sequence=[True] * 10 + [False])

    t0 = time.monotonic()
    bounded_proc_shutdown(proc, grace_s=0.2, term_s=0.5)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"SIGTERM path took {elapsed:.3f}s"
    assert calls["killpg"] == [(proc.pid, signal.SIGTERM)]
    assert not proc.terminate_called, "SIGKILL should not be needed"


def test_child_survives_term_triggers_sigkill(monkeypatch):
    """Child outlives grace+term → terminate(force=True) gets called."""
    calls = _stub_signal_calls(monkeypatch)
    # Stay alive through both windows.
    proc = FakeProc(alive_sequence=[True] * 1000, terminate_kills=True)

    t0 = time.monotonic()
    bounded_proc_shutdown(proc, grace_s=0.1, term_s=0.1)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"SIGKILL path took {elapsed:.3f}s"
    assert calls["killpg"] == [(proc.pid, signal.SIGTERM)]
    assert proc.terminate_called
    assert proc.terminate_force is True


def test_killpg_oserror_falls_back_to_pid_kill(monkeypatch):
    """getpgid raising → still send SIGTERM via os.kill(pid)."""
    pid_kill_calls: list[tuple[int, int]] = []

    def fake_getpgid(pid: int) -> int:
        raise ProcessLookupError("no such process")

    def fake_kill(pid: int, sig: int) -> None:
        pid_kill_calls.append((pid, sig))

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (pid, 0)

    monkeypatch.setattr(pty_shutdown.os, "getpgid", fake_getpgid)
    monkeypatch.setattr(pty_shutdown.os, "kill", fake_kill)
    monkeypatch.setattr(pty_shutdown.os, "waitpid", fake_waitpid)

    proc = FakeProc(alive_sequence=[True] * 10 + [False])
    bounded_proc_shutdown(proc, grace_s=0.05, term_s=0.5)

    assert pid_kill_calls == [(proc.pid, signal.SIGTERM)]


def test_waitpid_updates_exitstatus(monkeypatch):
    """Reaped child with WIFEXITED → proc.exitstatus set."""
    monkeypatch.setattr(pty_shutdown.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pty_shutdown.os, "killpg", lambda *_: None)
    # Pretend exit-status 42 packed in the standard way.
    monkeypatch.setattr(pty_shutdown.os, "waitpid", lambda *_: (99999, 42 << 8))

    proc = FakeProc(alive_sequence=[False])
    bounded_proc_shutdown(proc, grace_s=0.05, term_s=0.05)

    assert proc.exitstatus == 42
    assert proc.signalstatus is None


def test_waitpid_updates_signalstatus(monkeypatch):
    """Reaped child WIFSIGNALED → proc.signalstatus set.

    Note: passing raw ``signal.SIGTERM`` (15) as the status word works
    here because ``WIFSIGNALED(15) == True`` and ``WTERMSIG(15) == 15``
    — i.e. for low signal numbers the encoded status word coincides
    with the signal number. For higher signals (e.g. SIGKILL=9 is fine,
    but SIGSYS=12 with core-dump bit would differ), the kernel-encoded
    word would be different. This fixture intentionally exercises the
    low-signal coincidence to keep the test self-contained.
    """
    monkeypatch.setattr(pty_shutdown.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pty_shutdown.os, "killpg", lambda *_: None)
    monkeypatch.setattr(pty_shutdown.os, "waitpid", lambda *_: (99999, signal.SIGTERM))

    proc = FakeProc(alive_sequence=[False])
    bounded_proc_shutdown(proc, grace_s=0.05, term_s=0.05)

    assert proc.signalstatus == signal.SIGTERM
    assert proc.exitstatus is None


def test_waitpid_zero_means_still_alive_no_update(monkeypatch):
    """waitpid(WNOHANG) returning (0, 0) → no exitstatus update."""
    monkeypatch.setattr(pty_shutdown.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pty_shutdown.os, "killpg", lambda *_: None)
    monkeypatch.setattr(pty_shutdown.os, "waitpid", lambda *_: (0, 0))

    proc = FakeProc(alive_sequence=[False])
    bounded_proc_shutdown(proc, grace_s=0.05, term_s=0.05)

    assert proc.exitstatus is None
    assert proc.signalstatus is None


def test_waitpid_echild_swallowed(monkeypatch):
    """ECHILD on reap (already collected) → no exception bubbles up."""

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        raise ChildProcessError("ECHILD")

    monkeypatch.setattr(pty_shutdown.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pty_shutdown.os, "killpg", lambda *_: None)
    monkeypatch.setattr(pty_shutdown.os, "waitpid", fake_waitpid)

    proc = FakeProc(alive_sequence=[False])
    bounded_proc_shutdown(proc, grace_s=0.05, term_s=0.05)  # must not raise


def test_env_override_used_when_kwargs_none(monkeypatch):
    """Confirm env-override path (kwargs = None) reads the env."""
    monkeypatch.setenv("AI_HATS_PTY_GRACE_S", "0.05")
    monkeypatch.setenv("AI_HATS_PTY_TERM_S", "0.05")
    _stub_signal_calls(monkeypatch)

    proc = FakeProc(alive_sequence=[True] * 1000)

    t0 = time.monotonic()
    bounded_proc_shutdown(proc)  # no kwargs → must consult env
    elapsed = time.monotonic() - t0

    # 0.05 + 0.05 = 0.10 + scheduler slack; must be well under default 5+2.
    assert elapsed < 1.0, f"env-override ignored — took {elapsed:.3f}s"
    assert proc.terminate_called


def test_isalive_exception_treated_as_dead(monkeypatch):
    """ptyprocess sometimes raises on a dead child — treat as exited."""
    calls = _stub_signal_calls(monkeypatch)

    class FlakyProc(FakeProc):
        def isalive(self):
            raise OSError("ptyprocess weirdness")

    proc = FlakyProc()
    t0 = time.monotonic()
    bounded_proc_shutdown(proc, grace_s=1.0, term_s=1.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5
    assert calls["killpg"] == []


# ---------- emit_terminal_reset ----------------------------------------


def test_emit_terminal_reset_writes_expected_bytes_when_forced(tmp_path):
    """Verify the exact DECRST sequence reaches the fd (force=True)."""
    log = tmp_path / "out.bin"
    with log.open("wb") as fh:
        emit_terminal_reset(fh.fileno(), force=True)

    written = log.read_bytes()
    assert written == (
        b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
    )


def test_emit_terminal_reset_skips_non_tty_by_default(tmp_path):
    """Without force, redirected stdout (file/pipe) gets no bytes."""
    log = tmp_path / "out.bin"
    with log.open("wb") as fh:
        emit_terminal_reset(fh.fileno())  # not a tty → noop

    assert log.read_bytes() == b""


def test_emit_terminal_reset_writes_when_fd_is_real_tty():
    """openpty() slave fd is a tty → write happens without force."""
    master, slave = os.openpty()
    try:
        emit_terminal_reset(slave)  # no force; isatty(slave) is True
        # Read from master to confirm bytes flowed.
        # Use os.set_blocking(False) so this never deadlocks if write skipped.
        os.set_blocking(master, False)
        try:
            data = os.read(master, 1024)
        except BlockingIOError:
            data = b""
        assert data == (
            b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
        )
    finally:
        os.close(master)
        os.close(slave)


def test_emit_terminal_reset_tolerates_bad_fd():
    """EBADF on closed fd must not raise — best-effort cleanup."""
    r, w = os.pipe()
    os.close(w)
    # isatty on read-end-of-pipe is False → noop path, but must not raise.
    emit_terminal_reset(r)
    # Even with force, os.write to a half-closed pipe-read-fd raises EBADF
    # in the OSError branch and is swallowed.
    emit_terminal_reset(r, force=True)
    os.close(r)
