"""e2e (HATS-1192)

flow:   an agent running an interactive PTY session with custom PTY tap extensions
cmds:
    ai-hats execute -r assistant
expect: PTY tap tees output, injects input, and invokes close handler upon session
        teardown
why:    without PTY tap seam wiring, automated harnesses cannot inspect or inject PTY
        byte streams
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.surfaces

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Interactive child: echoes ``GOT:hello`` when it reads ``hello``, exits on ``quit``.
_CHILD_INTERACTIVE = textwrap.dedent(
    """\
    import os
    seen = b""
    while True:
        chunk = os.read(0, 1024)
        if not chunk:
            break
        seen += chunk
        if b"hello" in seen and b"\\x01" not in seen:
            os.write(1, b"GOT:hello\\n")
            seen += b"\\x01"
        if b"quit" in seen:
            break
    """
)

# Fast child: prints and exits immediately (for the fail-open path, where no tap
# exists to inject a ``quit``).
_CHILD_FAST = "import os; os.write(1, b'BYE\\n')"

_DRIVER = textwrap.dedent(
    """\
    import os, sys, threading, time
    sys.path[:0] = {src_roots!r}
    from ai_hats.runtime import WrapRunner

    RAISE = {raise_factory}
    CHILD = {child!r}

    class _Sess:
        def log_trace(self, *a, **k): pass
        def log_sys(self, *a, **k): pass
        def log_sub(self, *a, **k): pass
        def log_res(self, *a, **k): pass

    class _Tracer:
        def __init__(self): self.session = _Sess()
        def make_master_read(self): return lambda fd: os.read(fd, 65536)
        def make_stdin_read(self): return lambda fd: os.read(fd, 65536)

    events = []
    r, w = os.pipe()

    class _Tap:
        def __init__(self, *, inject, resize, session):
            self._inject = inject
            self._resize = resize
        def extra_read_fds(self): return [r]
        def on_output(self, data): events.append(("out", data))
        def on_readable(self, fd):
            if fd == r:
                self._inject(os.read(fd, 65536))
        def close(self): events.append(("close", None))

    def factory(*, inject, resize, session):
        if RAISE:
            raise RuntimeError("boom")
        return _Tap(inject=inject, resize=resize, session=session)

    def feed():
        time.sleep(0.6)
        os.write(w, b"hello\\n")
        time.sleep(0.4)
        os.write(w, b"quit\\n")
    if not RAISE:
        threading.Thread(target=feed, daemon=True).start()

    runner = WrapRunner.__new__(WrapRunner)
    rc = WrapRunner._pty_spawn(
        runner, [sys.executable, "-c", CHILD], {{}}, _Tracer(), pty_tap_factory=factory
    )

    got = b"".join(d for k, d in events if k == "out")
    closed = any(k == "close" for k, _ in events)
    sys.stderr.write("\\n__RC__=%s\\n" % rc)
    sys.stderr.write("__CLOSED__=%s\\n" % closed)
    sys.stderr.write("__GOT_HELLO__=%s\\n" % (b"GOT:hello" in got))
    sys.stderr.flush()
    """
)


def _run_driver(tmp_path: Path, *, raise_factory: bool, child: str) -> str:
    from _helpers.env import checkout_pythonpath

    src_roots = checkout_pythonpath(_REPO_ROOT).split(os.pathsep)
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER.format(src_roots=src_roots, raise_factory=raise_factory, child=child))
    proc = subprocess.run(  # noqa: S603 — fixed argv, our own driver
        [sys.executable, str(driver)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=25,
        text=True,
    )
    return proc.stderr


@pytest.mark.integration
def test_seam_tees_output_injects_input_and_closes(tmp_path):
    err = _run_driver(tmp_path, raise_factory=False, child=_CHILD_INTERACTIVE)
    assert "__RC__=0" in err, f"child did not exit cleanly (inject failed?):\n{err[-500:]}"
    assert "__GOT_HELLO__=True" in err, f"OUT tee / inject round-trip failed:\n{err[-500:]}"
    assert "__CLOSED__=True" in err, f"close() not called on teardown:\n{err[-500:]}"


@pytest.mark.integration
def test_factory_that_raises_is_fail_open(tmp_path):
    err = _run_driver(tmp_path, raise_factory=True, child=_CHILD_FAST)
    # A raising factory must not break the session: the child still runs to exit.
    assert "__RC__=0" in err, f"raising factory broke the session (not fail-open):\n{err[-500:]}"
    assert "__CLOSED__=False" in err, f"no tap should exist when the factory raised:\n{err[-500:]}"
