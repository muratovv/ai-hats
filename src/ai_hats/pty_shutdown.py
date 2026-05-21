"""Bounded shutdown for PTY-spawned children — HATS-411.

Background
----------
`PtyProcess.wait()` calls blocking `os.waitpid(pid, 0)`. On macOS,
Claude Code (and other libuv-backed children) sometimes get stuck in the
"trying to exit" state (`ps` STAT ``?Es``): JS heap is released, but
the kernel never transitions the process to true zombie because libuv
handles / detached worker threads are still open. `waitpid(pid, 0)`
then blocks **forever**.

Field evidence: 7 simultaneously-stuck Claude panes on 2026-05-20,
across versions 2.1.126 / 2.1.138 / 2.1.139 / 2.1.143 — see HATS-411
description for full incident report.

Fix
---
Replace the unbounded ``proc.wait()`` in ``runtime._pty_spawn`` with
:func:`bounded_proc_shutdown`. The escalation chain is:

1. Poll ``isalive`` up to ``grace_s`` (default 5.0).
2. ``SIGTERM`` the whole process group (``killpg``) — pokes libuv
   worker threads / MCP children whose handles block exit.
3. Poll again up to ``term_s`` (default 2.0).
4. ``SIGKILL`` via ``proc.terminate(force=True)``.
5. Non-blocking reap (``WNOHANG``); update ``proc.exitstatus`` /
   ``proc.signalstatus`` if reapable. Worst-case the PID slot leaks
   (zombie remains in process table) but the parent returns.

Configuration: ``AI_HATS_PTY_GRACE_S`` and ``AI_HATS_PTY_TERM_S``
(floats; invalid values fall back to the documented defaults).
"""

from __future__ import annotations

import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

# Defaults. SIGTERM gives libuv a window to flush handles; SIGKILL is
# the final escape hatch. Total worst-case wall-clock cost: grace + term
# + one polling tick (~50 ms) before the WNOHANG reap.
_DEFAULT_GRACE_S = 5.0
_DEFAULT_TERM_S = 2.0

# Polling cadence inside bounded_proc_shutdown. Small enough that the
# escalation feels snappy when the child does exit, large enough not to
# burn CPU. 50 ms matches the runtime.py select() granularity.
_POLL_INTERVAL_S = 0.05

# DECRST mouse-tracking off. Emitted by emit_terminal_reset() after a
# PTY child terminates so any mouse-tracking enabled by the child (and
# not turned off because the child crashed / hung mid-exit) does not
# leak into the outer terminal — visible symptom being raw SGR mouse
# reports (`^[[<35;…M`) printed as text in the surrounding shell pane.
#
#   ?1000l  — DEC mouse tracking off (X10 / VT200)
#   ?1002l  — button-event mouse tracking off
#   ?1003l  — any-event mouse tracking off
#   ?1006l  — SGR extended mouse mode off
#   ?1015l  — urxvt extended mouse mode off
_DECRST_MOUSE_RESET = (
    "\x1b[?1000l"
    "\x1b[?1002l"
    "\x1b[?1003l"
    "\x1b[?1006l"
    "\x1b[?1015l"
)


def _env_float(name: str, default: float) -> float:
    """Read a float env var with safe fallback.

    Non-positive, non-finite, or unparseable values fall back to *default*
    — never raise. This is configuration, not user input that should fail
    a session.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.debug("ignoring non-float %s=%r (using default %s)", name, raw, default)
        return default
    if value <= 0 or value != value or value == float("inf"):
        logger.debug("ignoring out-of-range %s=%r (using default %s)", name, raw, default)
        return default
    return value


def _poll_until_dead(proc, deadline: float) -> bool:
    """Poll ``proc.isalive`` until *deadline* (monotonic seconds).

    Returns True iff the child has exited by the deadline.
    """
    while time.monotonic() < deadline:
        try:
            if not proc.isalive():
                return True
        except Exception:  # noqa: BLE001 — ptyprocess can raise misc OSErrors
            return True
        time.sleep(_POLL_INTERVAL_S)
    try:
        return not proc.isalive()
    except Exception:  # noqa: BLE001
        return True


def _killpg_term(proc) -> None:
    """SIGTERM the child's process group, swallowing OSError.

    Tries the child's own pgroup first; falls back to single-pid SIGTERM
    if ``getpgid`` fails (the child may have raced into a zombie state
    already). Either path is safe — both are best-effort.
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _nonblocking_reap(proc) -> None:
    """Final WNOHANG reap; update proc.exitstatus / proc.signalstatus.

    Best-effort. ECHILD (already reaped) and any other OSError are
    swallowed — the goal is "parent returns", not perfect bookkeeping.
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        reaped_pid, status = os.waitpid(pid, os.WNOHANG)
    except OSError:
        return
    if reaped_pid == 0:
        # Child still alive (e.g. stuck in `?Es`). Leave zombie behind;
        # the parent's session will end and the OS reparents to init.
        return
    if os.WIFEXITED(status):
        proc.exitstatus = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        proc.signalstatus = os.WTERMSIG(status)


def bounded_proc_shutdown(
    proc,
    *,
    grace_s: float | None = None,
    term_s: float | None = None,
) -> None:
    """Bound a ptyprocess child's shutdown so the parent never hangs.

    Parameters
    ----------
    proc
        A ``ptyprocess.PtyProcess`` (or duck-typed equivalent) exposing
        ``isalive()``, ``terminate(force=...)``, ``pid``,
        ``exitstatus``, ``signalstatus``.
    grace_s
        Seconds to wait for natural exit before sending SIGTERM-pgroup.
        ``None`` reads ``AI_HATS_PTY_GRACE_S`` env (default 5.0).
    term_s
        Seconds to wait between SIGTERM-pgroup and SIGKILL.
        ``None`` reads ``AI_HATS_PTY_TERM_S`` env (default 2.0).

    Returns
    -------
    None
        Never blocks longer than ``grace_s + term_s + _POLL_INTERVAL_S``
        plus signal/reap syscalls.
    """
    if grace_s is None:
        grace_s = _env_float("AI_HATS_PTY_GRACE_S", _DEFAULT_GRACE_S)
    if term_s is None:
        term_s = _env_float("AI_HATS_PTY_TERM_S", _DEFAULT_TERM_S)

    # Stage 1: grace.
    if _poll_until_dead(proc, time.monotonic() + grace_s):
        _nonblocking_reap(proc)
        return

    # Stage 2: SIGTERM the whole pgroup — pokes libuv worker threads /
    # MCP children whose open handles are keeping the child in `?Es`.
    _killpg_term(proc)
    if _poll_until_dead(proc, time.monotonic() + term_s):
        _nonblocking_reap(proc)
        return

    # Stage 3: SIGKILL (via ptyprocess so it also closes the master fd).
    try:
        proc.terminate(force=True)
    except Exception:  # noqa: BLE001 — ptyprocess raises misc on dead children
        pass

    # Stage 4: best-effort reap. If the child is truly stuck in `?Es`
    # even SIGKILL won't move it (kernel exit-cleanup blocked). We
    # accept the zombie and return — the parent process exits, the
    # pane becomes recoverable, init eventually reaps.
    _nonblocking_reap(proc)


def emit_terminal_reset(fd: int = 1) -> None:
    """Write DECRST mouse-tracking off to *fd*.

    Called from the PTY parent on its own stdout AFTER the child has
    terminated. The parent's stdout is the OUTER terminal (the tmux
    pane / Ghostty window), so this clears mouse-tracking state that
    the dead child may have left enabled — preventing raw SGR mouse
    reports from rendering as visible text in the surrounding shell.

    Tolerates closed-fd / broken-pipe — best-effort cleanup.

    Note: writing to the dead child's SLAVE pty (as a user-side
    recovery via ``printf > /dev/ttysNNN``) is **the wrong path** —
    delivery lands in the slave's input queue on macOS and zsh then
    interprets the bytes as commands (lesson from HATS-411 work_log
    2026-05-21T04:17). Always emit DECRST on the parent's own stdout.
    """
    try:
        os.write(fd, _DECRST_MOUSE_RESET.encode("ascii"))
    except OSError:
        # EBADF (closed), EPIPE (other end gone), or similar — drop.
        pass
