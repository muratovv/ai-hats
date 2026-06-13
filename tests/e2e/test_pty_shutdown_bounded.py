"""End-to-end coverage for bounded PTY shutdown — HATS-411.

The unit suite (``tests/test_pty_shutdown.py``) exercises the escalation
contract with a ``FakeProc`` and monkeypatched signals — fast, but
cannot catch the original bug where ``ptyprocess.wait()`` blocks on real
``os.waitpid(pid, 0)`` against a real macOS exit-pending child.

This module spawns real ``ptyprocess.PtyProcess`` children and asserts:

1. ``bounded_proc_shutdown`` returns within the configured deadline
   (in-test elapsed assertions; pytest-timeout is NOT a project dep,
   so a true hang relies on CI job-level wall-clock to catch),
2. ``emit_terminal_reset`` writes DECRST bytes when the fd is a real
   TTY and skips when redirected to a file (isatty guard),
3. ``WrapRunner._pty_spawn`` end-to-end exercises the wired
   ``bounded_proc_shutdown`` call site without raising.

Children are **not** perfect macOS `?Es` reproducers — that path
requires Claude-grade libuv-handle leak at the kernel level — but
cover the broader "child ignores SIGTERM" + wired-path-import-works
failure shapes.

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


# Fast cooperative child for the wired-path smoke test. Prints banner,
# exits cleanly after a short delay. Goal: drive a real PtyProcess
# through WrapRunner._pty_spawn end-to-end so the import / call site
# of bounded_proc_shutdown is actually executed.
#
# Scope limitation (acknowledged): this is a smoke test for the wire,
# not a revert-detection guard. We cannot reproduce the original macOS
# `?Es` exit-stall from pure Python — that bug requires kernel-level
# libuv handle leak. A revert that re-introduced blocking
# ``proc.wait()`` would still pass this test because the child exits
# cleanly. Honest naming: this catches import errors, call-site
# removal, and bounded_proc_shutdown raising on already-dead children.
WIRED_CHILD_SOURCE = textwrap.dedent(
    """\
    import sys, time
    sys.stdout.write("wired_child up\\n")
    sys.stdout.flush()
    time.sleep(0.2)
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
    not whatever the editable install at /Users/.../ai-hats points at.

    HATS-562: snapshot + restore sys.modules entries we evict so subsequent
    tests (which captured module-level references at collection time) do not
    end up monkeypatching a stale module object while production code
    re-imports a fresh one. Without restoration, every smoke/integration test
    that does ``monkeypatch.setattr(auto_retro, ...)`` after this fixture
    runs silently patches a dead module and the production code path keeps
    using the original function.
    """
    src = Path(__file__).resolve().parent.parent.parent / "src"
    monkeypatch.syspath_prepend(str(src))
    # Snapshot the cached imports we are about to drop so we can put the
    # ORIGINAL module objects back on teardown — other tests still hold
    # references to them.
    snapshot = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "ai_hats" or name.startswith("ai_hats.")
    }
    for name in snapshot:
        del sys.modules[name]
    try:
        from ai_hats import pty_shutdown
        yield pty_shutdown
    finally:
        # Drop whatever the fixture's body imported, then restore the
        # original cache so module-level references in other tests stay
        # valid.
        for name in list(sys.modules):
            if name == "ai_hats" or name.startswith("ai_hats."):
                del sys.modules[name]
        sys.modules.update(snapshot)


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
def test_e2e_emit_terminal_reset_writes_to_real_tty(_import_pty_shutdown):
    """emit_terminal_reset writes the DECRST bytes when fd is a real TTY.

    Uses ``os.openpty()`` rather than ``os.pipe()`` so the isatty guard
    inside ``emit_terminal_reset`` passes naturally — covers the runtime
    contract (a session's stdout IS a tty).
    """
    master, slave = os.openpty()
    try:
        _import_pty_shutdown.emit_terminal_reset(slave)
        os.set_blocking(master, False)
        try:
            data = os.read(master, 1024)
        except BlockingIOError:
            data = b""
    finally:
        os.close(master)
        os.close(slave)

    assert data == (
        b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
    )


@pytest.mark.integration
def test_e2e_emit_terminal_reset_skips_non_tty(tmp_path, _import_pty_shutdown):
    """Redirected stdout (file) → emit_terminal_reset writes nothing.

    Prevents log pollution when running ``ai-hats run > out.log`` under
    CI / batch automation.
    """
    log = tmp_path / "out.bin"
    with log.open("wb") as fh:
        _import_pty_shutdown.emit_terminal_reset(fh.fileno())
    assert log.read_bytes() == b""


@pytest.mark.integration
def test_e2e_pty_spawn_wired_path_executes_bounded_shutdown(
    tmp_path, _import_pty_shutdown,
):
    """Smoke test: WrapRunner._pty_spawn finally block invokes the new wire.

    Drives a real PtyProcess through ``_pty_spawn`` end-to-end (no mocks),
    confirming that the runtime.py imports + finally-block calls to
    ``bounded_proc_shutdown`` / ``emit_terminal_reset`` work without
    raising — i.e. the wire is in place and the call site is reachable.

    Coverage gap (documented honestly): this is NOT a revert-detection
    test. A hypothetical revert that re-introduced the old
    ``proc.terminate(force=True)`` + blocking ``proc.wait()`` block
    would still pass — our cooperative child exits in 200 ms, so
    blocking wait reaps it instantly. The original macOS ``?Es``
    exit-stall (kernel-level libuv handle leak) is the only failure
    shape that detects the regression at e2e level, and it cannot be
    reproduced from pure Python userspace.
    """
    from ai_hats.observe import Session, SidecarTracer
    from ai_hats.runtime import WrapRunner

    child_path = _write_child(tmp_path, "wired_child.py", WIRED_CHILD_SOURCE)

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="hats-411-wired", session_dir=session_dir)
    tracer = SidecarTracer(session)

    # Bypass WrapRunner.__init__ — _pty_spawn needs only (cmd, env, tracer).
    runner = object.__new__(WrapRunner)

    t0 = time.monotonic()
    exit_code = runner._pty_spawn([sys.executable, str(child_path)], {}, tracer)
    elapsed = time.monotonic() - t0

    # Cooperative child sleeps 0.2s; wire overhead is sub-millisecond.
    assert elapsed < 3.0, f"_pty_spawn took {elapsed:.3f}s — bounded path regression?"

    # Strict exit-code check: cooperative child exits cleanly, so the
    # bounded shutdown's WNOHANG reap MUST succeed and set exitstatus=0.
    # A 124 here would mean bounded_proc_shutdown silently regressed
    # (failed to reap a reapable child) — caught explicitly rather than
    # masked under the round-1 permissive `{0, 124}` set.
    assert exit_code == 0, (
        f"expected 0 from cooperative child; got {exit_code} — bounded shutdown regression?"
    )


@pytest.mark.integration
def test_e2e_pty_spawn_returns_124_when_shutdown_unresolved(
    tmp_path, monkeypatch, _import_pty_shutdown,
):
    """HATS-411 Major-1 fix: _pty_spawn returns 124 when shutdown leaves no status.

    Simulates the macOS `?Es` exit-stall outcome by monkeypatching
    ``bounded_proc_shutdown`` to a no-op — neither ``exitstatus`` nor
    ``signalstatus`` get set on proc. Pre-fix, runtime.py returned 0
    in this branch (silent success masking a leaked zombie); post-fix,
    it returns 124 (GNU `timeout` convention).
    """
    # HATS-715: WrapRunner moved to wrap_runner — patch there (where _pty_spawn looks).
    from ai_hats import wrap_runner as runtime_mod
    from ai_hats.observe import Session, SidecarTracer
    from ai_hats.wrap_runner import WrapRunner

    child_path = _write_child(tmp_path, "wired_child.py", WIRED_CHILD_SOURCE)

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="hats-411-unresolved", session_dir=session_dir)
    tracer = SidecarTracer(session)

    # No-op shutdown: leaves proc.exitstatus / signalstatus as None.
    monkeypatch.setattr(runtime_mod, "bounded_proc_shutdown", lambda *_a, **_k: None)
    # Also stub emit_terminal_reset so we don't write to test stdout.
    monkeypatch.setattr(runtime_mod, "emit_terminal_reset", lambda *_a, **_k: None)

    runner = object.__new__(WrapRunner)
    exit_code = runner._pty_spawn([sys.executable, str(child_path)], {}, tracer)

    # The child does exit cleanly, but our stubbed shutdown didn't reap
    # it via WNOHANG, so neither exitstatus nor signalstatus is set
    # before we reach runtime.py:831-839 — exercises the 124 branch.
    assert exit_code == 124, (
        f"expected 124 (unresolved shutdown), got {exit_code}"
    )

    # Reap any leaked zombies — the stubbed shutdown skipped the WNOHANG
    # reap, so the child sits in the process table until init reparents.
    # Drain politely now so `pytest -x` loops don't accumulate zombies.
    while True:
        try:
            reaped, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if reaped == 0:
            break
