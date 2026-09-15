"""The session-time writer of ``events.jsonl``.

One writer per session, started before the surface is launched and closed after
it exits. It follows the surface's own record through the surface's reader and
appends each canonical event the moment the reader yields it. Nothing rewrites
the file afterwards — this is the only thing that writes it — so a consumer
following it during the run and one reading it later see the same record. The
surface hands in WHERE its record is (``locate``) and HOW to read it
(``reader_factory``); nothing here names a provider.

The run's own lifecycle is the writer's to say: ``RunStarted`` is the first line,
written before the surface can produce anything, and ``RunEnded`` the last,
written after the record is drained — a fault in the follow costs the events
after it, never the ending.
"""  # comment-length: allow — one writer, one file, its two lifecycle lines: the contract

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .canonical.events import RunEnded, RunStarted
from .canonical.reader import EventReader
from .canonical.types import now
from .event_log import append_event, write_events

Locate = Callable[[], Sequence[Path]]
ReaderFactory = Callable[[Path], EventReader]
Report = Callable[[str], None]


@dataclass(frozen=True)
class EventLogOutcome:
    """What the writer leaves behind when the session is over."""

    events_written: int
    # The first fault, as text. A writer that faulted stopped at that tick, so
    # the file is complete up to it and silent after — the trace says which.
    error: str | None = None
    # How many sources were ever located. Zero with no error is its own finding:
    # the writer ran the whole session and the record it was told to follow
    # never appeared where it was told to look.
    sources: int = 0


class EventLogWriter:
    """Follow a session's record and append its events as they happen."""

    def __init__(
        self,
        *,
        locate: Locate,
        reader_factory: ReaderFactory,
        path: Path | str,
        interval_s: float = 0.25,
        report: Report | None = None,
    ) -> None:
        self._locate = locate
        self._reader_factory = reader_factory
        self._path = Path(path)
        self._interval_s = interval_s
        self._report = report
        self._readers: dict[Path, EventReader] = {}
        self._written = 0
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # A tick from the thread and the drain from close() must not overlap.
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def events_written(self) -> int:
        return self._written

    @property
    def error(self) -> str | None:
        return self._error

    # -- one pass ------------------------------------------------------------

    def tick(self) -> int:
        """Adopt any source that has appeared, append what each has produced
        since the last pass; return how many events were written."""
        with self._lock:
            return self._tick()

    def _tick(self) -> int:
        for source in self._locate():
            source = Path(source)
            if source not in self._readers:
                self._readers[source] = self._reader_factory(source)
        written = 0
        for reader in self._readers.values():
            # Materialized first: an empty pass must not open the file, and a
            # reader's offset has moved by the time its events are written.
            events = list(reader.read())
            if events:
                written += write_events(events, self._path, append=True)
        self._written += written
        return written

    # -- the thread ----------------------------------------------------------

    def start(self) -> EventLogWriter:
        """Say the run began, then follow it on a thread."""
        append_event(RunStarted(ts=now()), self._path)
        self._written += 1
        self._thread = threading.Thread(target=self._run, name="event-log-writer", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self.tick()
            except Exception as exc:  # the session must outlive its observer
                self._fault(f"{type(exc).__name__}: {exc}")
                return

    def _fault(self, text: str) -> None:
        if self._error is None:
            self._error = text
        if self._report is not None:
            self._report(f"events.jsonl writer stopped: {text}")

    def close(self, *, exit_code: int | None = None, timeout_s: float = 5.0) -> EventLogOutcome:
        """Declare the run over: stop the thread, tell every reader its source
        is finished, drain what a live reader was holding back, and say so.

        ``exit_code`` is how the surface ended as the harness saw it; ``None``
        when the harness never learned one. The ending is written even after a
        fault, and a fault in writing it is reported like any other.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None
        if self._error is None:
            try:
                with self._lock:
                    # Adopt a source that appeared since the last pass BEFORE
                    # ending the readers: one adopted during the drain would
                    # never be told the run is over.
                    self._tick()
                    for reader in self._readers.values():
                        reader.close()
                    self._tick()
            except Exception as exc:  # same contract as the thread: report, never raise
                self._fault(f"{type(exc).__name__}: {exc}")
        try:
            append_event(
                RunEnded(
                    ok=exit_code == 0,
                    raw_code=None if exit_code is None else str(exit_code),
                    detail=self._error,
                    ts=now(),
                ),
                self._path,
            )
            self._written += 1
        except Exception as exc:  # the ending is the last thing that may fail; still reported
            self._fault(f"{type(exc).__name__}: {exc}")
        return EventLogOutcome(
            events_written=self._written, error=self._error, sources=len(self._readers)
        )


__all__ = ["EventLogOutcome", "EventLogWriter"]
