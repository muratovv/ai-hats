"""Two-phase subscriber dispatcher + dispatch journal (HATS-1020).

In-lock = blocking, exception aborts before the single persist; post-lock =
reaction after persist + lock release, failures journaled, never swallowed.
Full contract: this package's README (epic HATS-1014 §2.1–2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .errors import RackConfigError, RackError
from .events import Event
from .models import TaskCard, utc_now

if TYPE_CHECKING:
    from .fsm import Topology
    from .kernel import Kernel


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
class Set:
    """Replace a declared field's value wholesale (a :class:`Delta` field op)."""

    value: Any

    def apply(self, card: TaskCard, name: str) -> None:
        card.set_field(name, self.value)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "set", "value": self.value}


@dataclass(frozen=True)
class Append:
    """Append an entry to a declared list field (a :class:`Delta` field op)."""

    entry: Any

    def apply(self, card: TaskCard, name: str) -> None:
        card.append_field(name, self.entry)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "append", "entry": self.entry}


#: A declared-field mutation op carried in ``Delta.fields``.
FieldOp = Set | Append


@dataclass(frozen=True)
class Delta:
    """Card mutation requested by an in-lock subscriber, applied by the kernel
    in-memory before the single persist. Post-lock deltas are journal-only.

    ``fields`` carries declared-field ops keyed by field name (:class:`Set` /
    :class:`Append`): a typed TaskCard field is validated against its type, an
    unknown key rides the extras passthrough — applied in the same single
    persist as ``work_log`` (HATS-1043, ADR-0017 §4)."""

    work_log: tuple[str, ...] = ()
    fields: Mapping[str, FieldOp] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"work_log": list(self.work_log)}
        if self.fields:
            d["fields"] = {name: op.to_dict() for name, op in self.fields.items()}
        return d


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
    """Extension contract: declare subscriptions, react to events.

    Two OPTIONAL lifecycle hooks a subscriber MAY expose (duck-typed — the
    composition root probes with ``hasattr``, so implementing neither keeps a
    subscriber valid): ``bind(kernel)`` — a post-lock kernel handle wired after
    construction (see :class:`BindableSubscriber`); ``requires_states() ->
    frozenset[str]`` — the subscriber's full semantic state vocabulary, checked
    against the composed topology fail-closed (HATS-1043 R8, ADR-0017 §3)."""

    name: str

    def subscriptions(self) -> Sequence[Subscription]: ...

    def on_event(self, ctx: DispatchContext) -> Delta | None: ...


@runtime_checkable
class BindableSubscriber(Subscriber, Protocol):
    """A :class:`Subscriber` that also needs a post-lock kernel handle: the
    composition root calls ``bind`` after the kernel is constructed (ADR-0017
    §4). ``DispatchContext`` stays kernel-free — IN_LOCK handlers never get a
    handle (one lock, never nested)."""

    def bind(self, kernel: Kernel) -> None: ...


def bind_subscribers(subscribers: Sequence[Subscriber], kernel: Kernel) -> None:
    """Composition-root bind loop: run the optional ``bind(kernel)`` hook on
    every subscriber exposing it (ADR-0017 §4), uniformly over all subscribers."""
    for sub in subscribers:
        bind = getattr(sub, "bind", None)
        if bind is not None:
            bind(kernel)


def validate_requires_states(
    subscribers: Sequence[Subscriber], topology: Topology, *, source: str
) -> None:
    """Fail-closed composition check: every subscriber's optional
    ``requires_states()`` vocabulary must be a subset of the composed topology,
    else refuse — naming the subscriber, the missing states, and the topology
    source (HATS-1043 R8; the HATS-692 stranding class, caught at composition)."""
    known = set(topology.states)
    for sub in subscribers:
        declared = getattr(sub, "requires_states", None)
        if declared is None:
            continue
        missing = sorted(set(declared()) - known)
        if missing:
            raise RequiresStatesError(sub.name, missing, sorted(known), source)


class AbortOperation(Exception):
    """In-lock subscriber verdict: block the operation, with an actionable
    reason the CLI shows to the agent (the reason channel)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OperationAborted(RackError):
    """Typed abort raised by the dispatcher: names the subscriber and carries
    its actionable reason. Nothing was persisted."""

    def __init__(self, event_key: str, subscriber: str, reason: str) -> None:
        self.event_key = event_key
        self.subscriber = subscriber
        self.reason = reason
        super().__init__(f"{event_key} aborted by '{subscriber}': {reason}")


class RequiresStatesError(RackConfigError):
    """A subscriber declares required states absent from the composed topology
    (HATS-1043 R8). Structural composition invariant — routed to the internal
    marker like the rest of the RackConfigError subtree."""

    def __init__(
        self, subscriber: str, missing: Sequence[str], known: Sequence[str], source: str
    ) -> None:
        self.subscriber = subscriber
        self.missing = tuple(missing)
        self.known = tuple(known)
        self.source = source
        super().__init__(
            f"subscriber '{subscriber}' requires state(s) {list(self.missing)} absent from "
            f"the topology ({source}); known states: {list(self.known)}"
        )


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
    #: fate of the operation — "persisted" | "aborted"; only the kernel knows
    #: it (deriving from outcomes would lie on a failed persist). K7 audit.
    result: str = ""
    #: structured event payload (edge from/to, epicify child, pre-destroy
    #: operation) — lossless beyond the bare key. K7 audit.
    detail: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event": self.event_key,
            "task_id": self.task_id,
            "actor": self.actor,
            "force": self.force,
            "reason": self.reason,
            "started_at": self.started_at,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "result": self.result,
        }
        if self.detail:
            d["detail"] = dict(self.detail)
        return d


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

    def _subscribers_for_event(self, event: Event, phase: Phase) -> list[Subscriber]:
        """Subscribers for an event's match keys, merged into one priority order.

        A named edge (HATS-1042 §3) matches the canonical ``edge:<from>--<to>``
        key AND the alias ``edge:<name>``; both buckets interleave by the single
        (priority, registration) order, so alias and canonical subscribers share
        one total order rather than firing in separate passes.
        """
        alias = getattr(event, "alias_key", None)
        if not alias:
            return self.subscribers_for(event.key, phase)
        merged = self._index.get((event.key, phase), []) + self._index.get((alias, phase), [])
        return [sub for _, _, sub in sorted(merged, key=lambda item: (item[0], item[1]))]

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
        for sub in self._subscribers_for_event(event, Phase.IN_LOCK):
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
            if delta is not None and (delta.work_log or delta.fields):
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
        for sub in self._subscribers_for_event(event, Phase.POST_LOCK):
            try:
                delta = sub.on_event(make_ctx())
            except Exception as exc:  # noqa: BLE001 — reported via the journal
                outcomes.append(
                    SubscriberOutcome(sub.name, Phase.POST_LOCK, "error", reason=repr(exc))
                )
                continue
            if delta is not None and (delta.work_log or delta.fields):
                # POST_LOCK deltas are journal-only — the persist already
                # happened, so fields are recorded, never applied (ADR-0017 §4).
                outcomes.append(SubscriberOutcome(sub.name, Phase.POST_LOCK, "delta", delta=delta))
            else:
                outcomes.append(SubscriberOutcome(sub.name, Phase.POST_LOCK, "ok"))
