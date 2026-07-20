"""Field-owning extensions for HYP/PROP (HATS-1044, ADR-0017 §4/§5).

``hyp-verdicts`` owns ``validation_log`` and ``prop-votes`` owns ``votes``: both
append via ``Delta.fields`` on the composite-transition path, so an append can
ride a state change under ONE lock/persist (the tracker's append-then-set-status
atomicity). ``hyp-verdicts`` also drives the quorum autoclose sweep. Both are
ambient subscribers with no subscriptions — they exist for their python API and
field ownership, reached via ``Workspace.extension``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..dispatch import Append
from ..fsm import InvalidTransitionError
from ..models import TaskCard, utc_now
from ..ops import FieldsOp, StateOp
from .quorum import (
    AUTO_SESSION_ID,
    AUTOCLOSE_ACTOR,
    DEFAULT_QUORUM_K,
    QuorumClosure,
    quorum_closures,
)


def _active_logs(tasks_dir: Path) -> Iterable[tuple[str, list]]:
    """``(id, validation_log)`` for every ACTIVE card in the catalog; a corrupt
    card is skipped, not fatal (the listing tolerance, HATS-1024)."""
    if not tasks_dir.is_dir():
        return
    for path in sorted(tasks_dir.glob("*/task.yaml")):
        try:
            card = TaskCard.from_yaml(path)
        except Exception:  # noqa: BLE001, S112 — one corrupt card must not sink the sweep
            continue
        if card.state == "active":
            yield card.id, list(card.extras.get("validation_log") or [])


class HypVerdictsExtension:
    """Owns ``validation_log``; the append + autoclose python API (ADR-0017 §5)."""

    name = "hyp-verdicts"

    def __init__(self) -> None:
        self._kernel: Any = None

    def subscriptions(self):
        return ()

    def on_event(self, ctx):
        return None

    def bind(self, kernel: Any) -> None:
        self._kernel = kernel

    # ----- append API (io.append_verdict / append_then_set_status parity) -----

    def append_verdict(self, hyp_id: str, entry: Mapping[str, Any], *, actor: str, caller_cwd: Path):
        """Append one validation_log entry atomically (no state change)."""
        return self._kernel.transition_ops(
            hyp_id,
            [FieldsOp({"validation_log": Append(dict(entry))})],
            actor=actor,
            caller_cwd=caller_cwd,
        )

    def append_then_set_status(
        self,
        hyp_id: str,
        entry: Mapping[str, Any],
        *,
        to_state: str,
        actor: str,
        caller_cwd: Path,
        reason: str = "",
    ):
        """Append a verdict AND ride the transition to ``to_state`` under ONE
        lock/persist. Returns ``None`` when the edge is no longer legal from the
        card's current state (the only_if_status concurrency/idempotency guard)."""
        try:
            return self._kernel.transition_ops(
                hyp_id,
                [FieldsOp({"validation_log": Append(dict(entry))}), StateOp(to_state)],
                actor=actor,
                caller_cwd=caller_cwd,
                reason=reason,
            )
        except InvalidTransitionError:
            return None

    # ----- quorum autoclose sweep (workspace/pipeline API) --------------------

    def find_quorum_closures(self, *, k: int = DEFAULT_QUORUM_K) -> list[QuorumClosure]:
        """Active HYPs with at least ``k`` independent refuted sessions."""
        return quorum_closures(_active_logs(self._kernel.tasks_dir), k)

    def autoclose(
        self,
        *,
        caller_cwd: Path,
        k: int = DEFAULT_QUORUM_K,
        actor: str = AUTOCLOSE_ACTOR,
        dry_run: bool = False,
        now: str | None = None,
    ) -> list[QuorumClosure]:
        """Close every quorum-reached active HYP (append a synthetic sentinel
        entry + refute, atomically). Idempotent (only-active scan + sentinel
        exclusion), dry-run returns the closures without writing. A closure the
        atomic guard skips (already closed) is excluded from the result."""
        closures = self.find_quorum_closures(k=k)
        if dry_run:
            return closures
        closed: list[QuorumClosure] = []
        for closure in closures:
            result = self.append_then_set_status(
                closure.hyp_id,
                _synthetic_entry(closure, now=now),
                to_state="refuted",
                actor=actor,
                caller_cwd=caller_cwd,
                reason="quorum autoclose",
            )
            if result is not None:
                closed.append(closure)
        return closed


def _synthetic_entry(closure: QuorumClosure, *, now: str | None) -> dict[str, Any]:
    stamp = now or utc_now()
    return {
        "date": stamp[:10],
        "verdict": "refuted",
        "evidence": (
            f"auto-closed: quorum K={closure.k} reached — independent refuted "
            f"sessions: {', '.join(closure.refute_sessions)}"
        ),
        "recommendation": "close_refuted",
        "session_id": AUTO_SESSION_ID,
        "timestamp": stamp,
    }


class PropVotesExtension:
    """Owns ``votes``; appends one vote atomically via ``Delta.fields``."""

    name = "prop-votes"

    def __init__(self) -> None:
        self._kernel: Any = None

    def subscriptions(self):
        return ()

    def on_event(self, ctx):
        return None

    def bind(self, kernel: Any) -> None:
        self._kernel = kernel

    def add_vote(self, prop_id: str, vote: Mapping[str, Any], *, actor: str, caller_cwd: Path):
        """Append one vote entry atomically (io.add_vote parity)."""
        return self._kernel.transition_ops(
            prop_id,
            [FieldsOp({"votes": Append(dict(vote))})],
            actor=actor,
            caller_cwd=caller_cwd,
        )
