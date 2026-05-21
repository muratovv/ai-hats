"""End-to-end coverage for bounded PTY shutdown — HATS-411.

The unit suite (``tests/test_pty_shutdown.py``) exercises the escalation
contract with a ``FakeProc`` and monkeypatched signals — fast, but
cannot catch the original bug where ``ptyprocess.wait()`` blocks on real
``os.waitpid(pid, 0)`` against a real macOS exit-pending child.

This test spawns a real ``ptyprocess.PtyProcess`` against a real Python
``stuck_child.py`` and asserts that:

1. ``bounded_proc_shutdown`` returns within the configured deadline,
2. the test never hangs (pytest-timeout 10s),
3. ``emit_terminal_reset`` writes DECRST bytes to the captured fd.

The child is **not** a perfect macOS `?Es` reproducer — that path
requires Claude-grade libuv handles — but it does cover the broader
"child ignores SIGTERM" failure shape, which is the same code path the
fix protects.

Marker: ``integration`` (real PTY, real signals).
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest


# Stuck-child simulator: traps SIGTERM and silently swallows it for the
# first 30s. Only SIGKILL ends the process within the window. This
# triggers the escalation path grace → SIGTERM → SIGKILL → reap.
STUCK_CHILD_SOURCE = textwrap.dedent(
    """\
    import signal, sys, time
    # Silently swallow SIGTERM — simulates a libuv-stuck child that
    # the kernel cannot deliver clean exit cleanup to.
    signal.signal(signal.SIGTERM, lambda *_: None)
    sys.stdout.write("stuck_child up\\n")
    sys.stdout.flush()
    time.sleep(30)
    """
)


# Cooperative child: exits on SIGTERM. Used for the happy-path check.
COOPERATIVE_CHILD_SOURCE = textwrap.dedent(
    """\
    import signal, sys, time
    def _bye(*_):
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)
    sys.stdout.write("cooperative up\\n")
    sys.stdout.flush()
    time.sleep(30)
    """
)


def _write_child(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source)
    return p


@pytest.fixture
def _import_pty_shutdown(monkeypatch):
    """Insert worktree src/ onto sys.path so we exercise THIS branch's code,
    not whatever the editable install at /Users/.../ai-hats points at."""
    src = Path(__file__).resolve().parent.parent.parent / "src"
    monkeypatch.syspath_prepend(str(src))
    # Drop any cached import from the global editable install.
    for mod in list(sys.modules):
        if mod == "ai_hats" or mod.startswith("ai_hats."):
            del sys.modules[mod]
    from ai_hats import pty_shutdown
    return pty_shutdown


@pytest.mark.integration
def test_e2e_bounded_shutdown_kills_stuck_child(tmp_path, _import_pty_shutdown):
    """Real PtyProcess child ignoring SIGTERM → SIGKILL reaches it within deadline."""
    from ptyprocess import PtyProcess

    child_path = _write_child(tmp_path, "stuck_child.py", STUCK_CHILD_SOURCE)
    proc = PtyProcess.spawn([sys.executable, str(child_path)])

    # Drain initial output so the child is past its startup signal install.
    deadline = time.monotonic() + 2.0
    saw_banner = False
    while time.monotonic() < deadline and not saw_banner:
        try:
            chunk = proc.read(64)
        except (EOFError, OSError):
            break
        if b"stuck_child up" in chunk:
            saw_banner = True
    assert saw_banner, "stuck_child never reached its sleep loop"

    pid = proc.pid

    t0 = time.monotonic()
    _import_pty_shutdown.bounded_proc_shutdown(
        proc, grace_s=0.3, term_s=0.3,
    )
    elapsed = time.monotonic() - t0

    # Worst case in this path: 0.3 (grace) + 0.3 (term) + 1 poll + reap.
    # Be generous to absorb CI scheduler jitter.
    assert elapsed < 2.0, f"bounded shutdown took {elapsed:.3f}s — should be ≤2s"

    # Confirm the process actually died — kill(0) raises ProcessLookupError.
    # Brief retry window for the kernel to finish exit-cleanup post-SIGKILL.
    death_deadline = time.monotonic() + 1.0
    is_dead = False
    while time.monotonic() < death_deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            is_dead = True
            break
        time.sleep(0.05)
    assert is_dead, f"pid {pid} still alive after bounded_proc_shutdown"


@pytest.mark.integration
def test_e2e_bounded_shutdown_fast_path_cooperative(tmp_path, _import_pty_shutdown):
    """Child that respects SIGTERM exits in the SIGTERM stage — no SIGKILL needed."""
    from ptyprocess import PtyProcess

    child_path = _write_child(tmp_path, "coop_child.py", COOPERATIVE_CHILD_SOURCE)
    proc = PtyProcess.spawn([sys.executable, str(child_path)])

    # Wait for banner so the SIGTERM handler is installed.
    deadline = time.monotonic() + 2.0
    saw_banner = False
    while time.monotonic() < deadline and not saw_banner:
        try:
            chunk = proc.read(64)
        except (EOFError, OSError):
            break
        if b"cooperative up" in chunk:
            saw_banner = True
    assert saw_banner

    t0 = time.monotonic()
    _import_pty_shutdown.bounded_proc_shutdown(
        proc, grace_s=0.2, term_s=1.0,
    )
    elapsed = time.monotonic() - t0

    # Grace expires (child still in sleep, ignoring nothing), SIGTERM
    # fires, child exits — well within grace + a fraction of term.
    assert elapsed < 1.5, f"cooperative shutdown took {elapsed:.3f}s"


@pytest.mark.integration
def test_e2e_emit_terminal_reset_writes_to_real_pipe(_import_pty_shutdown):
    """emit_terminal_reset writes the DECRST bytes to a real pipe fd."""
    r, w = os.pipe()
    try:
        _import_pty_shutdown.emit_terminal_reset(w)
        os.close(w)
        data = os.read(r, 1024)
    finally:
        try:
            os.close(r)
        except OSError:
            pass

    # All five sequences present and concatenated in the documented order.
    assert data == (
        b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
    )
