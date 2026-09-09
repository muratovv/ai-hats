"""FD-gated PTY tap implementation for ai-hats (HATS-1197).

Permits an outer process (e.g. a relay server or daemon) to drive an interactive
HITL session PTY over file descriptors passed via environment variables
``AI_HATS_PTY_IN_FD`` and ``AI_HATS_PTY_OUT_FD``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from typing import Any, Callable

from .constants import ENV_PTY_IN_FD, ENV_PTY_OUT_FD

logger = logging.getLogger(__name__)

T_RAW = 0x00  # opaque terminal bytes, both directions (keystrokes IN / TUI OUT)
T_CTRL = 0x01  # JSON control message, e.g. {"resize": {"cols": C, "rows": R}}

_HEADER = 5
_MAX_PAYLOAD = 1 << 20  # 1 MiB guard


def wire_encode(ftype: int, payload: bytes) -> bytes:
    """Serialize one frame."""
    return bytes([ftype]) + len(payload).to_bytes(4, "big") + payload


def wire_raw(payload: bytes) -> bytes:
    """Encode a RAW frame."""
    return wire_encode(T_RAW, payload)


def wire_ctrl(obj: dict) -> bytes:
    """Encode a CTRL frame."""
    return wire_encode(T_CTRL, json.dumps(obj).encode("utf-8"))


def wire_resize(cols: int, rows: int) -> bytes:
    """Encode a CTRL resize frame."""
    return wire_ctrl({"resize": {"cols": cols, "rows": rows}})


class WireFrameParser:
    """Incremental parser: feed raw socket/pipe bytes, get whole frames back."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """Append ``data`` and return every complete ``(type, payload)`` available.

        Raises ``ValueError`` on a length that exceeds the guard.
        """
        self._buf.extend(data)
        out: list[tuple[int, bytes]] = []
        while len(self._buf) >= _HEADER:
            ftype = self._buf[0]
            length = int.from_bytes(self._buf[1:5], "big")
            if length > _MAX_PAYLOAD:
                raise ValueError(f"frame length {length} exceeds guard {_MAX_PAYLOAD}")
            if len(self._buf) < _HEADER + length:
                break
            payload = bytes(self._buf[_HEADER : _HEADER + length])
            del self._buf[: _HEADER + length]
            out.append((ftype, payload))
        return out


def parse_fd_env(env_var: str) -> int | None:
    """Parse integer file descriptor from environment variable.

    Returns None if the variable is unset or unparseable.
    """
    val = os.environ.get(env_var, "").strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def resolve_pty_fds() -> tuple[int | None, int | None]:
    """Resolve (in_fd, out_fd) from environment variables.

    Rules:
    - Both set -> (in_fd, out_fd)
    - Only IN set -> (in_fd, in_fd)
    - Only OUT set -> (out_fd, out_fd)
    - Neither set -> (None, None)
    """
    in_fd = parse_fd_env(ENV_PTY_IN_FD)
    out_fd = parse_fd_env(ENV_PTY_OUT_FD)

    if in_fd is not None and out_fd is not None:
        return in_fd, out_fd
    if in_fd is not None:
        return in_fd, in_fd
    if out_fd is not None:
        return out_fd, out_fd
    return None, None


class FdPtyTap:
    """PTY tap operating directly on file descriptors (in_fd / out_fd)."""

    def __init__(
        self,
        *,
        inject: Callable[[bytes], None],
        resize: Callable[[int, int], None],
        session: Any,
        in_fd: int | None = None,
        out_fd: int | None = None,
        close_fds: bool = False,
    ) -> None:
        self._inject = inject
        self._resize = resize
        self._session = session
        self._in_fd = in_fd
        self._out_fd = out_fd
        self._close_fds = close_fds

        self._parser = WireFrameParser()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)

        self._pending_r = False
        self._closed = False

        try:
            signal.signal(signal.SIGTERM, self._on_sigterm)
        except ValueError:
            pass

    def _on_sigterm(self, _sig: int, _frm: Any) -> None:
        self.close()

    def extra_read_fds(self) -> list[int]:
        if self._closed:
            return []
        fds = [self._wake_r]
        if self._in_fd is not None:
            fds.append(self._in_fd)
        return fds

    def on_output(self, data: bytes) -> None:
        if self._closed or not data or self._out_fd is None:
            return
        frame = wire_raw(data)
        try:
            os.write(self._out_fd, frame)
        except OSError:
            pass

    def on_readable(self, fd: int) -> None:
        if self._closed:
            return

        if fd == self._wake_r:
            try:
                os.read(self._wake_r, 1024)
            except OSError:
                pass
            if self._pending_r:
                self._pending_r = False
                # Deliberately unguarded, like the direct _inject calls in
                # _handle_raw_input — a swallowed failure here drops the user's
                # Enter with no trace.
                self._inject(b"\r")
            return

        if fd == self._in_fd:
            try:
                chunk = os.read(self._in_fd, 65536)
            except OSError:
                chunk = b""

            if not chunk:
                return

            try:
                frames = self._parser.feed(chunk)
            except ValueError:
                return

            for ftype, payload in frames:
                if ftype == T_RAW:
                    self._handle_raw_input(payload)
                elif ftype == T_CTRL:
                    self._handle_ctrl_input(payload)

    def _handle_raw_input(self, payload: bytes) -> None:
        if not payload:
            return

        if self._session and hasattr(self._session, "append_audit"):
            ts = time.time()
            self._session.append_audit(f"remote_input bytes={len(payload)} ts={ts}")

        if len(payload) > 1 and payload.endswith(b"\r"):
            head = payload[:-1]
            self._inject(head)
            self._pending_r = True
            try:
                os.write(self._wake_w, b"\x00")
            except OSError:
                pass
        else:
            self._inject(payload)

    def _handle_ctrl_input(self, payload: bytes) -> None:
        try:
            msg = json.loads(payload)
            rz = msg.get("resize")
            if rz and "rows" in rz and "cols" in rz:
                self._resize(int(rz["rows"]), int(rz["cols"]))
        except (ValueError, KeyError, TypeError):
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        try:
            os.close(self._wake_r)
        except OSError:
            pass
        try:
            os.close(self._wake_w)
        except OSError:
            pass

        if self._close_fds:
            if self._in_fd is not None:
                try:
                    os.close(self._in_fd)
                except OSError:
                    pass
            if self._out_fd is not None and self._out_fd != self._in_fd:
                try:
                    os.close(self._out_fd)
                except OSError:
                    pass


def make_fd_pty_tap(
    *, inject: Callable[[bytes], None], resize: Callable[[int, int], None], session: Any
) -> FdPtyTap | None:
    """Factory creating an FdPtyTap instance if PTY FDs are set in environment."""
    in_fd, out_fd = resolve_pty_fds()
    if in_fd is None and out_fd is None:
        return None
    return FdPtyTap(
        inject=inject,
        resize=resize,
        session=session,
        in_fd=in_fd,
        out_fd=out_fd,
    )
