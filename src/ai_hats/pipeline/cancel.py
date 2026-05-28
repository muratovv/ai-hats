"""Cooperative cancellation primitive for pipeline execution.

A `CancelToken` is a thread-safe signal threaded through the pipeline as a
`PipelineContext` sidecar. The runner flips it on timeout or external
cancel; steps read it to cancel cooperatively.
"""

from __future__ import annotations

import threading
from enum import Enum


class CancelReason(Enum):
    """Why a `CancelToken` was cancelled."""

    TIMEOUT = "timeout"
    """A step exceeded its configured timeout."""

    EXTERNAL = "external"
    """An external caller cancelled the pipeline."""

    PROPAGATED = "propagated"
    """Cancellation propagated from a composite/child step."""


class CancelToken:
    """Thread-safe, one-shot cancellation signal.

    Idempotent: the first `cancel(reason)` wins; later calls are no-ops.
    Safe to read (`cancelled`, `reason`) from any thread.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: CancelReason | None = None

    def cancel(self, reason: CancelReason) -> None:
        """Cancel the token. The first reason wins; later calls are no-ops."""
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> CancelReason | None:
        return self._reason
