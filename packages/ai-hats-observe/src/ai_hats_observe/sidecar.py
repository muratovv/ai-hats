"""PTY sidecar tracer — intercepts master/stdin fds into trace.log (HATS-948, T15).

``SidecarTracer`` wraps ``pty.spawn`` read callbacks, stripping ANSI/Zellij
chrome and appending ``[RES]``/``[REQ]`` trace lines through the injected
``Session``. Depends only on observe's own vocab leaf + stdlib.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .trace import TraceTag

if TYPE_CHECKING:
    from .session import Session


class SidecarTracer:
    """PTY sidecar: intercepts master/stdin fds and logs [RES]/[REQ] to trace."""

    ANSI_ESCAPE = re.compile(rb"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
    ZELLIJ_NOISE = re.compile(rb"(?:[>|]+Zellij\(\d+\))+[a-z]{0,2}")

    def __init__(self, session: Session) -> None:
        self.session = session
        self._req_buf = bytearray()
        # Raw byte dump is opt-in: tens of MB per long session and may capture
        # sensitive content. Set AI_HATS_PTY_RAW_DUMP=1 to enable when
        # diagnosing PTY/terminal issues like HATS-220.
        self._raw_dump_enabled = os.environ.get("AI_HATS_PTY_RAW_DUMP") == "1"
        self._raw_fp = None  # lazily opened on first dump when enabled

    def _raw_dump(self, direction: bytes, data: bytes) -> None:
        """HATS-220 diagnostic: append raw bytes to pty_raw.log.

        Disabled by default. Enable via env: ``AI_HATS_PTY_RAW_DUMP=1``.
        Format: ``\\n[HH:MM:SS.mmm <direction>]<raw bytes>``. Records are
        delimited by the leading ``\\n[`` pattern; raw bytes are preserved
        verbatim so CSI escapes survive. Use ``grep -aE`` to search.
        """
        if not self._raw_dump_enabled or not data:
            return
        if self._raw_fp is None:
            try:
                self._raw_fp = open(self.session.pty_raw_path, "ab", buffering=0)
            except OSError:
                self._raw_fp = False  # sentinel — give up; don't retry
                return
        if self._raw_fp is False:
            return
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3].encode()
        try:
            self._raw_fp.write(b"\n[" + ts + b" " + direction + b"]" + data)
        except OSError:
            pass

    def strip_ansi(self, data: bytes) -> bytes:
        return self.ANSI_ESCAPE.sub(b"", data)

    def strip_noise(self, data: bytes) -> bytes:
        return self.ZELLIJ_NOISE.sub(b"", data)

    def make_master_read(self) -> Callable[[int], bytes]:
        """Returns master_read callback for pty.spawn — logs CLI output as [RES].

        Feeds ``trace.log`` only. The canonical audit source post-HATS-535
        is ``AuditWriter._parse_jsonl`` over the ``claude`` JSONL session
        log; ``trace.log`` is the JSONL-missing fallback parsed by
        ``AuditWriter._extract_turns`` / ``_extract_pio_content``.
        """
        def master_read(fd: int) -> bytes:
            data = os.read(fd, 1024)
            self._raw_dump(b"<<", data)
            cleaned = self.strip_ansi(data).strip()
            if cleaned:
                text = cleaned.decode("utf-8", errors="replace")
                self.session.log_trace(TraceTag.RES, text)
            return data
        return master_read

    _CONTEXT_CLEAR_RE = re.compile(r"^/clear\b")
    _CONTEXT_COMPACT_RE = re.compile(r"^/compact\b")

    def make_stdin_read(self) -> Callable[[int], bytes]:
        """Returns stdin_read callback for pty.spawn — logs user input as [REQ] on newline."""
        def stdin_read(fd: int) -> bytes:
            data = os.read(fd, 1024)
            self._raw_dump(b">>", data)
            self._req_buf.extend(data)
            if b"\n" in self._req_buf or b"\r" in self._req_buf:
                line = self.strip_noise(self.strip_ansi(bytes(self._req_buf))).strip()
                if line:
                    text = line.decode("utf-8", errors="replace")
                    self.session.log_trace(TraceTag.REQ, text)
                    # Detect context-changing slash commands
                    if self._CONTEXT_CLEAR_RE.match(text):
                        self.session.append_audit("🧹 Context cleared")
                        self.session.log_trace(TraceTag.SYS, "Context cleared by user")
                    elif self._CONTEXT_COMPACT_RE.match(text):
                        self.session.append_audit("🗜️ Context compacted")
                        self.session.log_trace(TraceTag.SYS, "Context compacted by user")
                self._req_buf.clear()
            return data
        return stdin_read
