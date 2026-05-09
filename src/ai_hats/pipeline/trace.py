"""Pipeline trace events + JSONL writer (HATS-274).

Opt-in observability: when a ``Pipeline.run`` call has an ``on_step``
callback, the inner loop fires one ``TraceEvent`` after every step
(success or halt-failure) — capturing what keys the step saw, what it
emitted, how long it took, and whether it raised.

By default events carry only **key names**, not values, so trace files
stay compact and never accidentally leak prompt contents or secrets to
disk. Callers opt in to value capture (``include_values=True``); long
strings get truncated via ``safe_repr`` (the same helper used by
``pre_log``/``post_log``).

The default writer (``JsonlTraceWriter``) appends one JSON line per
event and flushes after each — so a pipeline that crashes mid-flight
still leaves a readable trace on disk.

Concurrent writes:
    Per-run files (``<pipeline>-<ts>.jsonl``) make collisions unlikely.
    Even if two runs of the same pipeline share a second-precision
    timestamp, ``O_APPEND`` writes ≤ ``PIPE_BUF`` (4096 bytes) on POSIX
    are atomic — our events sit well under that limit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

_MAX_VALUE_REPR = 120


def safe_repr(value: Any) -> str:
    """Truncated ``repr`` — caps at 120 chars + ``[+N more chars]``."""
    s = repr(value)
    if len(s) <= _MAX_VALUE_REPR:
        return s
    return s[:_MAX_VALUE_REPR] + f"... [+{len(s) - _MAX_VALUE_REPR} more chars]"


@dataclass(frozen=True)
class TraceEvent:
    """One step's execution slice."""

    ts: str  # ISO8601 UTC
    step: str
    requires_seen: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None
    requires_values: dict[str, str] | None = None
    produces_values: dict[str, str] | None = None


TraceHook = Callable[[TraceEvent], None]


def make_event(
    step_name: str,
    requires_seen: Mapping[str, Any],
    produces: Mapping[str, Any],
    duration_ms: float,
    *,
    error: BaseException | None = None,
    include_values: bool = False,
) -> TraceEvent:
    """Construct a ``TraceEvent`` from a step's inputs/outputs."""
    return TraceEvent(
        ts=datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
        step=step_name,
        requires_seen=sorted(requires_seen),
        produces=sorted(produces),
        duration_ms=round(duration_ms, 3),
        error=f"{type(error).__name__}: {error}" if error is not None else None,
        requires_values=(
            {k: safe_repr(v) for k, v in requires_seen.items()}
            if include_values else None
        ),
        produces_values=(
            {k: safe_repr(v) for k, v in produces.items()}
            if include_values else None
        ),
    )


class JsonlTraceWriter:
    """Append JSONL trace events to a file, flushing after each line.

    Thin and disk-resilient: opens the file fresh for every event so a
    crashed pipeline still leaves a readable trace. Failure to write
    must NOT propagate — trace is best-effort instrumentation, not
    business logic. Errors are logged at WARN.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: TraceEvent) -> None:
        try:
            line = json.dumps(asdict(event), ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError as e:
            logger.warning("trace write failed (%s): %s", self.path, e)
