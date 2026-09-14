"""The chain's verdict, recorded into the session's ``events.jsonl``.

Every surface's dispatcher passes through ``hook_dispatch._say`` — in the wrapper
process when the resident server answers, in a hook process of its own otherwise
— and both find the session the same way, from the identity envelope in the
environment. The line is appended the way the session's own writer appends, so
the two producers never interleave. Fail-open: a verdict that cannot be recorded
is said on stderr and changes nothing about the answer the surface gets.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ai_hats_observe.artifacts import EVENT_LOG_JSONL
from ai_hats_observe.canonical import GateDecision, GatePoint, GateVerdict, Timestamp, ToolCallId
from ai_hats_observe.event_log import append_event

from ..session_identity import SessionIdentity
from .hook_channel import ChainVerdict, HookCall, HookEvent

#: What ``GateVerdict.source`` says when the chain itself spoke.
SOURCE = "chain"

_POINTS = {
    HookEvent.PRE_TOOL_USE: GatePoint.BEFORE_TOOL,
    HookEvent.POST_TOOL_USE: GatePoint.AFTER_TOOL,
}


def _now() -> Timestamp:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return Timestamp(stamp.replace("+00:00", "Z"))


def gate_verdict(
    verdict: ChainVerdict,
    event: HookEvent | None,
    calls: Sequence[HookCall],
    *,
    now: Callable[[], Timestamp] = _now,
) -> GateVerdict | None:
    """The canonical event for what the chain said; ``None`` for an arrival no
    gate point binds to, where nothing was judged."""
    point = _POINTS.get(event) if event is not None else None
    if point is None:
        return None
    call = calls[0] if calls else None
    call_id = call.payload.get("tool_use_id") if call is not None else None
    return GateVerdict(
        point=point,
        decision=GateDecision(verdict.decision.value),
        hook=verdict.hook,
        reason=verdict.reason,
        nudges=tuple((nudge.hook, nudge.text) for nudge in verdict.nudges),
        tool=call.tool or None if call is not None else None,
        call_id=ToolCallId(call_id) if isinstance(call_id, str) and call_id else None,
        source=SOURCE,
        ts=now(),
    )


def record_verdict(
    verdict: ChainVerdict,
    event: HookEvent | None,
    calls: Sequence[HookCall],
    environ: Mapping[str, str],
) -> Path | None:
    """Append the verdict to the session's event log; the path written, or
    ``None`` when this process runs in no session or the line could not land.

    Never raises: the answer to the surface must not depend on the record.
    """
    try:
        identity = SessionIdentity.from_env(dict(environ))
        if identity is None:
            return None
        recorded = gate_verdict(verdict, event, calls)
        if recorded is None:
            return None
        path = identity.session_dir / EVENT_LOG_JSONL
        append_event(recorded, path)
        return path
    except Exception as exc:
        print(
            f"ai-hats-hook: gate verdict not recorded: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return None


__all__ = ["SOURCE", "gate_verdict", "record_verdict"]
