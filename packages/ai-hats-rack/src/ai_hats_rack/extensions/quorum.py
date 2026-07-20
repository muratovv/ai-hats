"""Independent-session quorum for HYP safe auto-close (HATS-1044, ADR-0017 §5).

Semantics ported BYTE-FOR-BYTE from the tracker's ``hypothesis/quorum.py`` (no
import — import-hygiene): a HYP reaches quorum when its ``validation_log`` holds
K distinct REAL ``refuted`` session_ids — empty ids and the synthetic
``auto-quorum`` sentinel excluded, so a reopened card never self-tips. The gate
licenses only the AUTOMATION actor's close (a manual/HITL refute is never gated —
the ADR-0009 safe direction: a wrong auto-close costs one revive to undo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..dispatch import AbortOperation, DispatchContext, Phase

DEFAULT_QUORUM_K = 3

#: Session id stamped on the synthetic closure entry — excluded from the count so
#: a reopened HYP does not start pre-loaded with one vote of its own closure.
AUTO_SESSION_ID = "auto-quorum"

#: Actor identity of the autoclose sweep; the gate licenses ONLY this actor.
AUTOCLOSE_ACTOR = "rack:hyp-autoclose"


@dataclass(frozen=True)
class QuorumClosure:
    """An active hypothesis that has reached the refuted-verdict quorum."""

    hyp_id: str
    refute_sessions: tuple[str, ...]  # distinct real session ids, sorted
    k: int


def independent_refute_sessions(entries: Iterable[Any]) -> set[str]:
    """Distinct real sessions that recorded a ``refuted`` verdict — entries with
    no ``session_id`` and the ``auto-quorum`` sentinel are never counted."""
    out: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        sid = e.get("session_id")
        if e.get("verdict") == "refuted" and sid and sid != AUTO_SESSION_ID:
            out.add(sid)
    return out


def quorum_closures(
    cards: Iterable[tuple[str, Sequence[Any]]], k: int = DEFAULT_QUORUM_K
) -> list[QuorumClosure]:
    """Pure core: from ``(hyp_id, validation_log)`` pairs of the ACTIVE HYPs, the
    ones with at least ``k`` independent refuted verdicts (idempotent by the
    caller only scanning active cards — a closed HYP is never re-selected)."""
    closures: list[QuorumClosure] = []
    for hyp_id, log in cards:
        sessions = independent_refute_sessions(log)
        if len(sessions) >= k:
            closures.append(QuorumClosure(hyp_id, tuple(sorted(sessions)), k))
    return closures


class HypQuorumGate:
    """In-lock edge handler on ``active--refuted``: gates the AUTOMATION actor's
    auto-close behind K independent refuted sessions; a manual refute passes
    unconditionally (ADR-0009 safe direction)."""

    name = "hyp-quorum-gate"
    PHASE = Phase.IN_LOCK

    def __init__(self, min_independent_sessions: int = DEFAULT_QUORUM_K) -> None:
        self._k = min_independent_sessions

    def on_event(self, ctx: DispatchContext) -> None:
        if ctx.actor != AUTOCLOSE_ACTOR:
            return None  # only the autoclose sweep is gated; a manual refute is not
        sessions = independent_refute_sessions(ctx.task.extras.get("validation_log", []))
        if len(sessions) < self._k:
            raise AbortOperation(
                f"auto-close quorum not reached: {len(sessions)} independent refuted "
                f"session(s), need {self._k} (a manual refute is not gated)"
            )
        return None
