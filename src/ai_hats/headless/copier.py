"""Copy a session's ``events.jsonl`` to the holder's stdout from the first byte (ADR-0038 D2).

It tails the file rather than tee-ing the writer: gate verdicts and the holder's
own lines are appended by other writers, and stdout must hold all of them, in the
file's order.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable


class LogCopier:
    """Tail ``path`` into ``fd``, whole lines only, until :meth:`finish`."""

    def __init__(
        self,
        path: Path,
        fd: int,
        *,
        interval_s: float = 0.05,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self._path = path
        self._fd = fd
        self._interval_s = interval_s
        self._report = report
        self._offset = 0
        self._reader_gone = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> LogCopier:
        self._thread = threading.Thread(target=self._run, name="headless-log-copier", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self.copy()
            except Exception as exc:  # the session must outlive its copy; finish() retries
                if self._report is not None:
                    self._report(f"headless log copy failed: {type(exc).__name__}: {exc}")
                return

    def copy(self) -> int:
        """Copy every complete line appended since the last pass; return bytes copied."""
        with self._lock:
            if self._reader_gone or not self._path.exists():
                return 0
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
            cut = data.rfind(b"\n")
            if cut < 0:
                return 0
            chunk = memoryview(data[: cut + 1])
            try:
                while chunk:
                    chunk = chunk[os.write(self._fd, chunk) :]
            except BrokenPipeError:
                # The reader left; the log is still complete, and the session's
                # end is decided by stdin and signals, not by stdout.
                self._reader_gone = True
                if self._report is not None:
                    self._report("headless stdout reader went away; the log keeps going")
                return 0
            self._offset += cut + 1
            return cut + 1

    def finish(self, timeout_s: float = 5.0) -> None:
        """Stop tailing, copy what is left, and close stdout — the client's EOF."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None
        self.copy()
        try:
            os.close(self._fd)
        except OSError as exc:
            if self._report is not None:
                self._report(f"closing headless stdout failed: {exc}")


__all__ = ["LogCopier"]
