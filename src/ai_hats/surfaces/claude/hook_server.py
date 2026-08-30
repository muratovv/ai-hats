"""A dispatcher that is already running when the hook arrives.

The spawn model pays ~43 ms before any gate runs — a fresh interpreter and this
package's imports — and none of that work is per-call. The session's own wrapper
process holds it instead and answers over a unix socket in the session cache.
Every call still reads the manifest and still runs :func:`dispatch`, so what a
gate decides is unchanged; only who was already loaded is.

Absence is not a failure mode: the settings entry falls back to spawning the
dispatcher whenever the socket is missing, stale, or unreachable.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import socketserver
import logging
import stat
import sys
import threading
from pathlib import Path

from ..hook_dispatch import dispatch
from .channel import ClaudeChannel

logger = logging.getLogger(__name__)

#: Sockets live here, not in the session cache: a cache path is 87 bytes for a
#: plain checkout and 106 for a worktree, against AF_UNIX's 104 — measured, and
#: `bind` fails where the pin points. One directory per uid, 0700, so the mode
#: on the socket is not the only thing keeping other accounts out.
SOCKET_ROOT = Path("/tmp")  # noqa: S108 - short by requirement; sockets_dir owns the safety


def sockets_dir(root: Path | None = None) -> Path:
    """Where the sockets go. Pure: a dry-run resolves the pin, and a report that
    writes to disk is not a dry-run (HATS-1552). `make_sockets_dir` creates."""
    return (root or SOCKET_ROOT) / f"ai-hats-{os.getuid()}"


def make_sockets_dir() -> Path:
    """The directory, created 0700, or a refusal if it is not ours alone."""
    home = sockets_dir()
    home.mkdir(mode=0o700, exist_ok=True)
    info = home.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError(f"{home} is not a private directory of this user")
    return home


def socket_path(cache_dir: Path) -> Path:
    """Named for the session, short enough to bind."""
    digest = hashlib.sha256(str(cache_dir).encode()).hexdigest()[:16]
    return sockets_dir() / f"{digest}.sock"


class _Fanout:
    """One process, several calls at once: a thread that claimed a buffer writes
    into it, every other thread reaches the real stream untouched.

    Needed because a verdict is written to `sys.stdout` — process-global state
    that `redirect_stdout` would hand to whichever call finished last.

    Deliberately not an `io.TextIOBase`: the base class ANSWERS `isatty` and
    `fileno` (False, and a raise), so a terminal this stands in front of would
    stop looking like one. Everything but the two writing methods is delegated.
    """

    def __init__(self, real) -> None:
        self._real = real
        self._local = threading.local()

    def claim(self, buf: io.StringIO) -> None:
        self._local.buf = buf

    def release(self) -> None:
        self._local.buf = None

    def _stream(self):
        return getattr(self._local, "buf", None) or self._real

    def write(self, text: str) -> int:
        return self._stream().write(text)

    def flush(self) -> None:
        self._stream().flush()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class HookServer:
    """The resident dispatcher for one session. ``close()`` is idempotent."""

    def __init__(self, cache_dir: Path, environ: dict[str, str]) -> None:
        self.path = socket_path(cache_dir)
        self._environ = environ
        self._server: _Server | None = None
        self._out: _Fanout | None = None
        self._err: _Fanout | None = None

    def start(self) -> "HookServer":
        make_sockets_dir()
        self.path.unlink(missing_ok=True)  # safe-delete: ok ephemeral socket
        # Bound BEFORE the streams are proxied: a bind that raises must not
        # leave the session writing through a proxy with nothing behind it.
        server = _Server(str(self.path), _handler(self))
        # The socket answers for this session's gates; nobody else may ask.
        os.chmod(self.path, 0o600)
        self._out, self._err = _Fanout(sys.stdout), _Fanout(sys.stderr)
        sys.stdout, sys.stderr = self._out, self._err
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        # Only OUR proxy is unwound, and only while it is the one installed —
        # restoring someone else's would hand them a stream they never took.
        if self._out is not None and sys.stdout is self._out:
            sys.stdout = self._out._real
        if self._err is not None and sys.stderr is self._err:
            sys.stderr = self._err._real
        self._out = self._err = None
        self.path.unlink(missing_ok=True)  # safe-delete: ok ephemeral socket

    def __enter__(self) -> "HookServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def answer(self, payload: str) -> tuple[int, str, str]:
        """One call judged, with this thread's output kept to this thread."""
        out, err = io.StringIO(), io.StringIO()
        if self._out is None or self._err is None:
            raise RuntimeError("the resident dispatcher was asked before it started")
        self._out.claim(out)
        self._err.claim(err)
        try:
            status = dispatch(ClaudeChannel(), stdin=io.StringIO(payload), environ=self._environ)
        finally:
            self._out.release()
            self._err.release()
        return status, out.getvalue(), err.getvalue()


def _read_request(rfile) -> str:
    """The payload, without asking the client to half-close the socket.

    `nc` closes its write side on stdin EOF on some platforms and holds it open
    on others (and macOS spells `-N` as something else entirely), so the request
    ends where the JSON does — a rule no client has to know about.
    """
    buf = ""
    while True:
        line = rfile.readline()
        if not line:
            return buf
        buf += line.decode("utf-8", "replace")
        try:
            json.loads(buf)
        except ValueError:
            continue
        return buf


def _handler(host: HookServer):
    class Handler(socketserver.StreamRequestHandler):
        timeout = 5

        def handle(self) -> None:
            payload = _read_request(self.rfile)
            try:
                status, out, err = host.answer(payload)
            except Exception as exc:  # the client falls back to spawning, not past the gate
                status, out, err = (
                    2,
                    "",
                    f"ai-hats-claude-hook: resident dispatcher failed: {exc}\n",
                )
            # Status, the verdict as its single line, then stderr to the end —
            # the shape the client parses with two `read`s and a `cat`.
            self.wfile.write(f"{status}\n{out.strip()}\n{err}".encode())

    return Handler


__all__ = ["HookServer", "socket_path"]
