"""One live ai-hats session: the child process plus the byte seam into it.

The broker never imports ai-hats. It spawns the binary and hands it two things: a
socketpair carrying framed terminal traffic (the seam ai-hats exposes through
``AI_HATS_PTY_IN_FD`` / ``AI_HATS_PTY_OUT_FD``), and a PTY for the child's own stdio.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import signal
import socket
import struct
import termios

from . import wire

DEFAULT_TERM = "xterm-256color"
_READ_SIZE = 65536
# The child's own stdio is not relayed, but discarding it outright makes a session
# that dies during startup die silently — the reason is written exactly there.
STDIO_TAIL_BYTES = 8192


def set_winsize(fd: int, cols: int, rows: int) -> None:
    """Set the window size on a PTY master; the kernel SIGWINCHes the foreground group."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Session:
    """A spawned ai-hats process and the framed channel into it."""

    def __init__(self, proc, sock: socket.socket, master_fd: int) -> None:
        self._proc = proc
        self._sock = sock
        self._master_fd = master_fd
        self._parser = wire.SessionFrameParser()
        self._pending: list[bytes] = []
        self._eof = False
        self._closed = False
        self._draining = False
        self._stdio_tail = bytearray()
        self._start_drain()

    @property
    def stdio_tail(self) -> bytes:
        """The child's last words on its own stdio — why a failed start failed."""
        return bytes(self._stdio_tail)

    async def wait(self) -> int | None:
        """Reap the child and return its exit code."""
        with contextlib.suppress(Exception):
            return await self._proc.wait()
        return self._proc.returncode

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    @classmethod
    async def spawn(
        cls,
        argv: list[str],
        *,
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> Session:
        """Start ``argv`` wired to a fresh seam, sized to ``cols`` x ``rows``."""
        master_fd, slave_fd = os.openpty()
        set_winsize(master_fd, cols, rows)

        parent_sock, child_sock = socket.socketpair()
        child_fd = child_sock.fileno()
        os.set_inheritable(child_fd, True)

        child_env = dict(os.environ if env is None else env)
        # One socketpair end serves both directions; ai-hats accepts in_fd == out_fd.
        child_env["AI_HATS_PTY_IN_FD"] = str(child_fd)
        child_env["AI_HATS_PTY_OUT_FD"] = str(child_fd)
        child_env.setdefault("TERM", DEFAULT_TERM)
        # Its warnings go to stdio we do not relay and its skip-key can never arrive,
        # so the hold is pure dead time here — 12.0s to first byte, 1.1s without.
        child_env.setdefault("AI_HATS_STARTUP_HOLD", "0")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                pass_fds=(child_fd,),
                env=child_env,
                cwd=cwd,
                # Own process group, so teardown can signal the whole tree at once.
                start_new_session=True,
            )
        except BaseException:
            for fd in (master_fd, slave_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)
            parent_sock.close()
            child_sock.close()
            raise

        # The parent's copies must go, or the child's exit never surfaces as EOF.
        child_sock.close()
        os.close(slave_fd)

        parent_sock.setblocking(False)
        os.set_blocking(master_fd, False)
        return cls(proc, parent_sock, master_fd)

    def _start_drain(self) -> None:
        """Discard the child's own stdio as it arrives.

        Not optional: the relayed stream is the seam, but an undrained PTY fills and
        then the child blocks writing to it, which freezes the live session. Driven by
        readiness rather than a read loop — polling a non-blocking fd would spin a core
        per session for a stream we throw away.
        """
        asyncio.get_running_loop().add_reader(self._master_fd, self._on_stdio_readable)
        self._draining = True

    def _on_stdio_readable(self) -> None:
        try:
            data = os.read(self._master_fd, _READ_SIZE)
        except BlockingIOError:
            return
        except OSError:
            data = b""  # EIO on Linux when the slave side closes
        if not data:
            self._stop_drain()
            return
        self._stdio_tail.extend(data)
        del self._stdio_tail[:-STDIO_TAIL_BYTES]

    def _stop_drain(self) -> None:
        if not self._draining:
            return
        self._draining = False
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().remove_reader(self._master_fd)

    async def read(self) -> bytes | None:
        """Next chunk of terminal output, or ``None`` once the session has ended."""
        loop = asyncio.get_running_loop()
        while not self._pending:
            if self._eof:
                return None
            try:
                chunk = await loop.sock_recv(self._sock, _READ_SIZE)
            except OSError:
                chunk = b""
            if not chunk:
                self._eof = True
                return None
            for ftype, payload in self._parser.feed(chunk):
                if ftype == wire.T_RAW:
                    self._pending.append(payload)
        return self._pending.pop(0)

    async def send_input(self, data: bytes) -> None:
        """Deliver keystrokes to the session."""
        await self._send(wire.session_raw(data))

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the session's terminal; also the primitive that forces a repaint."""
        await self._send(wire.session_resize(cols, rows))

    async def _send(self, frame: bytes) -> None:
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        with contextlib.suppress(OSError):
            await loop.sock_sendall(self._sock, frame)

    async def close(self, grace: float = 5.0) -> None:
        """Signal the process group, reap, and release every descriptor. Idempotent."""
        if self._closed:
            return
        self._closed = True

        self._stop_drain()

        if self._proc.returncode is None:
            self._signal_group(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=grace)
            except (TimeoutError, asyncio.TimeoutError):
                self._signal_group(signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await self._proc.wait()

        with contextlib.suppress(OSError):
            self._sock.close()
        with contextlib.suppress(OSError):
            os.close(self._master_fd)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self._proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(ProcessLookupError, OSError):
                self._proc.send_signal(sig)
