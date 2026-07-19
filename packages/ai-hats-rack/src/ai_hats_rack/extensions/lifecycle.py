"""Stock lifecycle-stamp handlers (HATS-1043): declaration-bound, in-lock field
stamps that replace the old ``kernel._stamp_lifecycle`` hardcode (ADR-0017 §3).

They ride the single persist (in-lock) and own an ordinary schema field via
``Delta.fields`` — the loader supplies the edges from the ``on_enter`` /
``edges[].handlers`` slots, so these handlers hardcode no keys.
"""

from __future__ import annotations

from ..dispatch import Delta, DispatchContext, Phase, Set
from ..models import utc_now

_DEFAULT_FIELD = "completed_at"


class StampLifecycleHandler:
    """Stamps ``field`` with the current UTC time on ENTERING the declaring
    state (default ``completed_at``; HYP configures ``closed``, ADR-0017 §5)."""

    name = "stamp-lifecycle"
    PHASE = Phase.IN_LOCK

    def __init__(self, field: str = _DEFAULT_FIELD) -> None:
        self._field = field

    def on_event(self, ctx: DispatchContext) -> Delta:
        return Delta(fields={self._field: Set(utc_now())})


class ClearLifecycleHandler:
    """Clears ``field`` on the declared edge and logs the reopen note — the
    declarative heir of the kernel done→execute branch (semantics preserved:
    ``completed_at`` cleared, work_log carries "Reopened from done")."""

    name = "clear-lifecycle"
    PHASE = Phase.IN_LOCK

    def __init__(self, field: str = _DEFAULT_FIELD) -> None:
        self._field = field

    def on_event(self, ctx: DispatchContext) -> Delta:
        return Delta(work_log=("Reopened from done",), fields={self._field: Set("")})
