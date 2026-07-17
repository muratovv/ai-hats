"""Two-phase subscriber dispatcher + dispatch journal (HATS-1020).

In-lock = blocking, exception aborts before the single persist; post-lock =
reaction after persist + lock release, failures journaled, never swallowed.
Full contract: this package's README (epic HATS-1014 §2.1–2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from .events import Event
from .models import TaskCard, utc_now


class Phase(str, Enum):
    #: blocking window of the publishing operation (a transition's lock
    #: window, or an extension's own operation for published pre-events).
    IN_LOCK = "in-lock"
    #: reaction after persist + lock release; may use the kernel API
    #: (one task lock at a time, never nested — HATS-690 rule).
    POST_LOCK = "post-lock"


@dataclass(frozen=True)
class Subscription:
    """Interest declaration: exact event key + phase + priority (lower first)."""

    event_key: str
    phase: Phase
    priority: int = 100


@dataclass(frozen=True)
class Delta:
    """Card mutation requested by an in-lock subscriber, applied by the kernel
    in-memory before the single persist. Post-lock deltas are journal-only."""

    work_log: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"work_log": list(self.work_log)}


@dataclass(frozen=True)
class DispatchContext:
    """What a subscriber sees. ``task`` is a deep copy — mutations never reach
    the store; ``caller_cwd`` is mandatory (no subscriber reads Path.cwd(),
    HATS-840); ``is_epic`` is recomputed from the child-set on every dispatch
    (HATS-794/977/979); ``force`` is FSM-guard information, not a safety-off
    switch for subscribers (HATS-518/596/697)."""

    event: Event
    task: TaskCard
    caller_cwd: Path
    is_epic: bool
    actor: str
    force: bool = False
    reason: str = ""


@runtime_checkable
class Subscriber(Protocol):
    """Extension contract: declare subscriptions, react to events."""

    name: str

    def subscriptions(self) -> Sequence[Subscription]: ...

    def on_event(self, ctx: DispatchContext) -> Delta | None: ...


class AbortOperation(Exception):
    """In-lock subscriber verdict: block the operation, with an actionable
    reason the CLI shows to the agent (the reason channel)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OperationAborted(Exception):
    """Typed abort raised by the dispatcher: names the subscriber and carries
    its actionable reason. Nothing was persisted."""

    def __init__(self, event_key: str, subscriber: str, reason: str) -> None:
        self.event_key = event_key
        self.subscriber = subscriber
        self.reason = reason
        super().__init__(f"{event_key} aborted by '{subscriber}': {reason}")


@dataclass(frozen=True)
class SubscriberOutcome:
    subscriber: str
    phase: Phase
    outcome: str  # "ok" | "delta" | "abort" | "error"
    reason: str = ""
    delta: Delta | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "subscriber": self.subscriber,
            "phase": self.phase.value,
            "outcome": self.outcome,
        }
        if self.reason:
            d["reason"] = self.reason
        if self.delta is not None:
            d["delta"] = self.delta.to_dict()
        return d


@dataclass(frozen=True)
class DispatchRecord:
    """One dispatched event with the outcome of every subscriber that ran."""

    event_key: str
    task_id: str
    actor: str
    force: bool
    reason: str
    started_at: str = field(default_factory=utc_now)
    outcomes: tuple[SubscriberOutcome, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event_key,
            "task_id": self.task_id,
            "actor": self.actor,
            "force": self.force,
            "reason": self.reason,
            "started_at": self.started_at,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


class JournalSink(Protocol):
    """Persistence seam for dispatch records — consumer: K7 audit log.

    The kernel calls ``record`` once per dispatch, after lock release (also
    on aborted dispatches, so refusals stay auditable — PROP-004 lossless).
    """

    def record(self, record: DispatchRecord) -> None: ...


class Dispatcher:
    """Routes events to subscribers by exact key, per phase, priority order."""

    def __init__(self, subscribers: Sequence[Subscriber] = ()) -> None:
        self._index: dict[tuple[str, Phase], list[tuple[int, int, Subscriber]]] = {}
        for seq, sub in enumerate(subscribers):
            for spec in sub.subscriptions():
                self._index.setdefault((spec.event_key, spec.phase), []).append(
                    (spec.priority, seq, sub)
                )
        for bucket in self._index.values():
            bucket.sort(key=lambda item: (item[0], item[1]))

    def subscribers_for(self, event_key: str, phase: Phase) -> list[Subscriber]:
        return [sub for _, _, sub in self._index.get((event_key, phase), [])]

    def run_blocking(
        self,
        event: Event,
        make_ctx: Callable[[], DispatchContext],
        apply_delta: Callable[[Delta], None],
        outcomes: list[SubscriberOutcome],
    ) -> None:
        """IN_LOCK phase: abort-by-exception is the default and only mode
        (HATS-481 — no catch-and-warn for the blocking phase). ``outcomes`` is
        appended in place so the caller can journal a partial dispatch."""
        for sub in self.subscribers_for(event.key, Phase.IN_LOCK):
            try:
                delta = sub.on_event(make_ctx())
            except AbortOperation as exc:
                outcomes.append(
                    SubscriberOutcome(sub.name, Phase.IN_LOCK, "abort", reason=exc.reason)
                )
                raise OperationAborted(event.key, sub.name, exc.reason) from exc
            except Exception as exc:
                # Fail-loud parity through the seam (HATS-866/AC3): the raw
                # exception propagates; the journal still records it.
                outcomes.append(
                    SubscriberOutcome(sub.name, Phase.IN_LOCK, "error", reason=repr(exc))
                )
                raise
            if delta is not None and delta.work_log:
                apply_delta(delta)
                outcomes.append(SubscriberOutcome(sub.name, Phase.IN_LOCK, "delta", delta=delta))
            else:
                outcomes.append(SubscriberOutcome(sub.name, Phase.IN_LOCK, "ok"))

    def run_reactions(
        self,
        event: Event,
        make_ctx: Callable[[], DispatchContext],
        outcomes: list[SubscriberOutcome],
    ) -> None:
        """POST_LOCK phase: fail-soft but reported — an exception is journaled
        as an error outcome and never re-raised (the persist already happened)."""
        for sub in self.subscribers_for(event.key, Phase.POST_LOCK):
            try:
                delta = sub.on_event(make_ctx())
            except Exception as exc:  # noqa: BLE001 — reported via the journal
                outcomes.append(
                    SubscriberOutcome(sub.name, Phase.POST_LOCK, "error", reason=repr(exc))
                )
                continue
            if delta is not None and delta.work_log:
                outcomes.append(SubscriberOutcome(sub.name, Phase.POST_LOCK, "delta", delta=delta))
            else:
                outcomes.append(SubscriberOutcome(sub.name, Phase.POST_LOCK, "ok"))
