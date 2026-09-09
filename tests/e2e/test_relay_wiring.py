"""e2e (HATS-1197)

flow:   an agent running session with PTY relay stream active
cmds:
    ai-hats execute -r assistant
expect: PTY relay wires input/output channels allowing external inspection during
        execution
why: without PTY relay wiring, interactive agent terminal sessions cannot be monitored
     by external UI"""

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

_DRIVER = textwrap.dedent(
    """\
    import os, socket, sys, threading, time
    sys.path[:0] = {src_roots!r}
    from ai_hats.runtime import WrapRunner
    from ai_hats.pty_relay import make_fd_pty_tap, wire_raw, WireFrameParser, T_RAW

    CHILD = {child!r}

    parent_sock, child_sock = socket.socketpair()
    os.environ["AI_HATS_PTY_IN_FD"] = str(child_sock.fileno())
    os.environ["AI_HATS_PTY_OUT_FD"] = str(child_sock.fileno())

    class _Sess:
        def __init__(self): self.audits = []
        def log_trace(self, *a, **k): pass
        def log_sys(self, *a, **k): pass
        def log_sub(self, *a, **k): pass
        def log_res(self, *a, **k): pass
        def append_audit(self, entry): self.audits.append(entry)

    class _Tracer:
        def __init__(self): self.session = _Sess()
        def make_master_read(self): return lambda fd: os.read(fd, 65536)
        def make_stdin_read(self): return lambda fd: os.read(fd, 65536)

    client_result = {{}}

    def parent_thread():
        try:
            time.sleep(0.1)
            parent_sock.sendall(wire_raw(b"hello\\r"))

            parser = WireFrameParser()
            got_hello = False
            start = time.time()
            while time.time() - start < 5:
                buf = parent_sock.recv(4096)
                if not buf:
                    break
                for ftype, payload in parser.feed(buf):
                    if ftype == T_RAW and b"GOT:hello" in payload:
                        got_hello = True
                        break
                if got_hello:
                    break

            parent_sock.sendall(wire_raw(b"quit\\n"))
            parent_sock.close()
            client_result["got_hello"] = got_hello
        except Exception as exc:
            client_result["error"] = str(exc)

    t = threading.Thread(target=parent_thread, daemon=True)
    t.start()

    tracer = _Tracer()
    runner = WrapRunner.__new__(WrapRunner)
    rc = WrapRunner._pty_spawn(
        runner, [sys.executable, "-c", CHILD], {{}}, tracer, pty_tap_factory=make_fd_pty_tap
    )
    t.join(timeout=5)

    sys.stderr.write("\\n__RC__=%s\\n" % rc)
    sys.stderr.write("__CLIENT_GOT_HELLO__=%s\\n" % client_result.get("got_hello", False))
    sys.stderr.write("__AUDIT_COUNT__=%s\\n" % len(tracer.session.audits))
    sys.stderr.flush()
    """
)


@pytest.mark.integration
def test_relay_fd_wiring_end_to_end(tmp_path):
    from _helpers.env import checkout_pythonpath

    src_roots = checkout_pythonpath(_REPO_ROOT).split(os.pathsep)
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER.format(src_roots=src_roots, child=_CHILD_INTERACTIVE))
    proc = subprocess.run(
        [sys.executable, str(driver)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=25,
        text=True,
    )
    err = proc.stderr
    assert "__RC__=0" in err, f"child did not exit cleanly:\n{err[-500:]}"
    assert "__CLIENT_GOT_HELLO__=True" in err, (
        f"relay client did not get echoed output:\n{err[-500:]}"
    )
    assert "__AUDIT_COUNT__" in err and not err.endswith("__AUDIT_COUNT__=0"), (
        f"audit logs missing:\n{err[-500:]}"
    )
