"""Quorum-based safe auto-close for hypotheses (HATS-769).

Pure decision core: given a :class:`HypothesisStore`, find ``active`` HYPs that
have accumulated a quorum of *K independent* ``refuted`` verdicts (distinct
``session_id`` on the validation_log) and close them as gone (status
``refuted``).

This is the SAFE closure direction only (close-as-gone) — never auto-confirm /
auto-accept (those imply action and stay HITL per ADR-0007). The asymmetry that
licenses the automation: a wrong auto-close costs one ``set-status active`` to
undo, whereas a wrong auto-confirm acts on a phantom. Every closure is logged to
``validation_log`` (a synthetic ``auto-quorum`` entry naming the contributing
sessions) and is trivially reversible.

See ADR-0009 and the ``quorum_autoclose`` pipeline step
(``pipeline/steps/quorum_autoclose.py``) that drives this from ``finalize-hitl``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .io import HypothesisStore, utc_now
from .model import Hypothesis, ValidationLogEntry

DEFAULT_QUORUM_K = 3

#: Session id stamped on the synthetic closure entry. Excluded from the quorum
#: count so a *reopened* HYP does not start pre-loaded with one vote of its own
#: closure.
AUTO_SESSION_ID = "auto-quorum"


@dataclass(frozen=True)
class QuorumClosure:
    """An active hypothesis that has reached the refuted-verdict quorum."""

    hyp_id: str
    refute_sessions: tuple[str, ...]  # distinct real session ids, sorted
    k: int


def _independent_refute_sessions(hyp: Hypothesis) -> set[str]:
    """Distinct real sessions that recorded a ``refuted`` verdict.

    Entries without a ``session_id`` cannot establish independence and the
    synthetic ``auto-quorum`` sentinel is never counted.
    """
    return {
        e.session_id
        for e in hyp.validation_log
        if e.verdict == "refuted" and e.session_id and e.session_id != AUTO_SESSION_ID
    }


def find_quorum_closures(store: HypothesisStore, k: int = DEFAULT_QUORUM_K) -> list[QuorumClosure]:
    """Active HYPs with at least ``k`` independent ``refuted`` verdicts.

    Only ``active`` hypotheses are scanned, so an already-closed HYP is never
    re-selected — re-running the sweep is a no-op (idempotent).
    """
    closures: list[QuorumClosure] = []
    for hyp in store.list_active():
        sessions = _independent_refute_sessions(hyp)
        if len(sessions) >= k:
            closures.append(QuorumClosure(hyp.id, tuple(sorted(sessions)), k))
    return closures


def apply_closure(
    store: HypothesisStore,
    closure: QuorumClosure,
    *,
    now: datetime | None = None,
) -> Hypothesis | None:
    """Append a synthetic audit entry and flip status to ``refuted``, atomically.

    The append + status flip happen under a single filelock and only while the
    HYP is still ``active`` (``only_if_status="active"``). Returns ``None`` when
    the HYP was already closed by a concurrent/earlier sweep — so no duplicate
    synthetic entry. Reversal is a single ``ai-hats task hyp set-status
    --status active``.
    """
    stamp = now or utc_now()
    return store.append_then_set_status(
        closure.hyp_id,
        ValidationLogEntry(
            date=stamp.date(),
            verdict="refuted",
            evidence=(
                f"auto-closed: quorum K={closure.k} reached — independent "
                f"refuted sessions: {', '.join(closure.refute_sessions)}"
            ),
            recommendation="close_refuted",
            session_id=AUTO_SESSION_ID,
            timestamp=stamp,
        ),
        status="refuted",
        only_if_status="active",
    )


def autoclose_quorum(
    store: HypothesisStore,
    k: int = DEFAULT_QUORUM_K,
    *,
    now: datetime | None = None,
) -> list[QuorumClosure]:
    """Find every quorum-reached active HYP, close it, and return those closed.

    A closure skipped by the atomic guard (already closed by a concurrent
    sweep) is excluded from the returned list — callers report only real closes.
    """
    closed: list[QuorumClosure] = []
    for closure in find_quorum_closures(store, k):
        if apply_closure(store, closure, now=now) is not None:
            closed.append(closure)
    return closed
