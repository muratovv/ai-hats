"""The rack kernel: transactional transition engine over task.yaml (HATS-1020).

transition = FileLock → FSM-guard → in-memory mutation → two-phase dispatch →
SINGLE persist of task.yaml, last. A bare kernel (no subscribers) is a pure
FSM — the explicit contract inherited from HATS-866/AC4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from filelock import FileLock, Timeout

from .cardschema import CardSchema, ExtrasForbiddenError, RequiredFieldError, default_card_schema
from .dispatch import (
    Delta,
    DispatchContext,
    Dispatcher,
    DispatchRecord,
    JournalSink,
    Phase,
    Subscriber,
    SubscriberOutcome,
)
from .errors import RackError
from .events import EdgeEvent, EpicifyEvent, Event, LinkEvent, PreDestroyEvent, event_detail
from .fsm import Topology, load_topology
from .models import LINK_STORAGE_FIELDS, TaskCard, utc_now
from .registry import LinksRegistry, load_registry

# Single loud-fail timeout pattern (HATS-936 / epic §2.2 rule 5): a wait this
# long on a sub-second fs op means a stuck holder, not real contention.
LOCK_TIMEOUT = 30.0


def _field_value(task: TaskCard, name: str) -> Any:
    """Current value of a card field: a TaskCard column or an extras-resident key
    (a custom backlog's declared field that is not a kernel-anchor column)."""
    return getattr(task, name) if name in TaskCard._KNOWN_FIELDS else task.extras.get(name)


class UnknownTaskError(RackError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' not found")


class TaskExistsError(RackError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' already exists")


class ForceRequiresReasonError(RackError):
    def __init__(self) -> None:
        super().__init__("force=True requires a non-empty reason")


class LockTimeoutError(RackError):
    """A kernel lock could not be acquired: loud, actionable, never silent."""

    def __init__(self, lock_path: Path, what: str, timeout: float) -> None:
        self.lock_path = lock_path
        super().__init__(
            f"{what} blocked >{timeout:.0f}s on {lock_path} — a stuck rack "
            "process likely holds it. If safe, remove the lock file and retry."
        )


@dataclass(frozen=True)
class TaskTransition:
    """One applied state change (the TaskTransition delta pattern)."""

    task_id: str
    from_state: str
    to_state: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "from": self.from_state,
            "to": self.to_state,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class KernelResult:
    """Every mutating call returns the card, the typed list of transitions
    that happened, and the dispatch journal. ``ops`` carries the per-op result
    dicts of a composite transition (revert-info included); empty otherwise."""

    task: TaskCard
    transitions: tuple[TaskTransition, ...] = ()
    journal: tuple[DispatchRecord, ...] = ()
    ops: tuple[dict[str, Any], ...] = ()


class Kernel:
    """Task store + transition engine. ``subscribers=()`` → pure FSM."""

    def __init__(
        self,
        tasks_dir: Path,
        *,
        prefix: str = "HATS",
        topology: Topology | None = None,
        registry: LinksRegistry | None = None,
        edge_names: Mapping[tuple[str, str], str] | None = None,
        schema: CardSchema | None = None,
        subscribers: Sequence[Subscriber] = (),
        journal_sink: JournalSink | None = None,
        lock_timeout: float = LOCK_TIMEOUT,
        exists_checker: Callable[[str, str | None], bool] | None = None,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.prefix = prefix
        # Cross-backlog target-existence seam (ADR-0017 §2): a workspace injects
        # a checker so a `targets:` kind resolves the sibling catalog; None keeps
        # the catalog-local default and in-lock handlers stay workspace-blind.
        self._exists_checker = exists_checker
        self.topology = topology if topology is not None else load_topology()
        # Injected config, not hardcoded kinds (HATS-1028): children_of/is_epic
        # read the hierarchy kind the registry names, default `parent_task`.
        self.registry = registry if registry is not None else load_registry()
        # The card-field write gate (HATS-1035): create/transition validate
        # against it; the packaged tasks schema is the zero-config fallback.
        self._schema = schema if schema is not None else default_card_schema()
        # Declared edge names (HATS-1042 §3): (from, to) → name; empty by default
        # so an unnamed edge fires only its canonical key (zero behavior change).
        self._edge_names = dict(edge_names or {})
        self._dispatcher = Dispatcher(subscribers)
        self._sink = journal_sink
        self._lock_timeout = lock_timeout

    # ----- store primitives -------------------------------------------------

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "task.yaml"

    def target_exists(self, target_id: str, targets: str | None = None) -> bool:
        """Existence of a link target (ADR-0017 §2 seam): the workspace-injected
        cross-backlog checker when present, else catalog-local — a ``targets:``
        kind then only resolves inside this kernel's own catalog."""
        if self._exists_checker is not None:
            return self._exists_checker(target_id, targets)
        return self._task_path(target_id).exists()

    def get(self, task_id: str) -> TaskCard | None:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        return TaskCard.from_yaml(path)

    def _load(self, task_id: str) -> TaskCard:
        task = self.get(task_id)
        if task is None:
            raise UnknownTaskError(task_id)
        return task

    def _persist(self, task: TaskCard) -> None:
        self._task_path(task.id).parent.mkdir(parents=True, exist_ok=True)
        # The emit gate (schema when-set fields dropped when empty) runs at the
        # single persist, hung off the schema — TaskCard.to_dict stays untouched.
        task.save(self._task_path(task.id), transform=self._schema.emit_filter)

    def _task_lock(self, task_id: str) -> FileLock:
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(lock_path), timeout=self._lock_timeout)

    def children_of(self, task_id: str) -> list[str]:
        """Ids of cards whose hierarchy-parent is ``task_id``.

        The parent edge is whatever the registry names as the hierarchy kind
        (default ``parent_task``) — kind-blind by config, not a hardcoded field
        (HATS-1028). A dedicated-field kind keeps the regex prefilter (full parse
        avoided — the tracker's reverse scan).
        """
        if not self.tasks_dir.exists():
            return []
        hierarchy = self.registry.hierarchy_kind
        if hierarchy is None:
            return []
        if hierarchy.name in LINK_STORAGE_FIELDS:
            field = re.escape(hierarchy.name)
            pattern = re.compile(rf"^{field}:\s*['\"]?{re.escape(task_id)}['\"]?\s*$", re.MULTILINE)
            out: list[str] = []
            for card in sorted(self.tasks_dir.glob("*/task.yaml")):
                try:
                    if pattern.search(card.read_text(encoding="utf-8")):
                        out.append(card.parent.name)
                except OSError:
                    continue
            return out
        # A parent kind stored under `links:` has no cheap text prefilter — load
        # and ask the registry (rare config; the default stays on the fast path).
        out = []
        for card_path in sorted(self.tasks_dir.glob("*/task.yaml")):
            try:
                card = TaskCard.from_yaml(card_path)
            except (OSError, ValueError):
                continue
            if self.registry.parent_of(card) == task_id:
                out.append(card_path.parent.name)
        return out

    def is_epic(self, task_id: str) -> bool:
        """Category predicate, computed fresh from the CURRENT child-set on
        every dispatch — never frozen at acquire time (HATS-794/977/979)."""
        return bool(self.children_of(task_id))

    # ----- journal ----------------------------------------------------------

    def _finish_record(
        self,
        event: Event,
        task_id: str,
        actor: str,
        force: bool,
        reason: str,
        outcomes: list[SubscriberOutcome],
        *,
        result: str,
    ) -> DispatchRecord:
        record = DispatchRecord(
            event_key=event.key,
            task_id=task_id,
            actor=actor,
            force=force,
            reason=reason,
            outcomes=tuple(outcomes),
            result=result,
            detail=event_detail(event),
        )
        if self._sink is not None:
            # Sink failures are loud by design: silently dropping audit
            # records is the truncation class PROP-004 forbids.
            self._sink.record(record)
        return record

    # ----- mutating API -----------------------------------------------------

    def create(
        self,
        *,
        actor: str,
        caller_cwd: Path,
        task_id: str | None = None,
        title: str = "",
        description: str | None = None,
        priority: str | None = None,
        role: str | None = None,
        reviewer: str | None = None,
        parent_task: str = "",
        depends_on: Sequence[str] = (),
        tags: Sequence[str] | None = None,
    ) -> KernelResult:
        """Create a card. Id allocation + reserve is atomic under the
        directory-scoped alloc lock (HATS-936); timeout is a loud failure.

        ``title`` is the only required input (ADR-0017 §1); the schema fields
        (``None`` sentinels) resolve to their declared defaults and are validated
        write-strict — a bad choice/type/required field is a typed refusal."""
        if not title.strip():
            raise RequiredFieldError("title", "a task requires a non-empty title")
        if parent_task and parent_task == task_id:
            raise ValueError(f"Task '{task_id}' cannot be its own parent")
        resolved = self._schema.resolve_create(
            {"description": description, "priority": priority, "role": role,
             "reviewer": reviewer, "tags": tags}
        )
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        # Not a `<prefix>-N` card dir, so scans ignore it.
        alloc_lock_path = self.tasks_dir / ".alloc.lock"
        lock = FileLock(str(alloc_lock_path), timeout=self._lock_timeout)
        try:
            with lock:
                if task_id is None:
                    task_id = self._next_id()
                if self._task_path(task_id).exists():
                    raise TaskExistsError(task_id)
                now = utc_now()
                task = TaskCard(
                    id=task_id,
                    title=title,
                    state=self.topology.initial,
                    parent_task=parent_task,
                    depends_on=list(depends_on),
                    created=now,
                    updated=now,
                    **resolved,
                )
                self._persist(task)
        except Timeout as exc:
            raise LockTimeoutError(
                alloc_lock_path, "task-id allocation", self._lock_timeout
            ) from exc
        journal = self._dispatch_epicify(parent_task, task.id, actor=actor, caller_cwd=caller_cwd)
        return KernelResult(task=task, journal=journal)

    def _next_id(self) -> str:
        max_num = 0
        for d in self.tasks_dir.iterdir():
            if d.is_dir():
                match = re.search(rf"{self.prefix}-(\d+)", d.name)
                if match:
                    max_num = max(max_num, int(match.group(1)))
        return f"{self.prefix}-{max_num + 1:03d}"

    def transition(
        self,
        task_id: str,
        to_state: str,
        *,
        actor: str,
        caller_cwd: Path,
        force: bool = False,
        reason: str = "",
        resolution: str | None = None,
        final_state: str | None = None,
    ) -> KernelResult:
        """Move a task along an FSM edge.

        ``force`` relaxes ONLY the FSM arrow (never subscriber safety) and
        requires a reason. ``resolution`` / ``final_state`` ride the same lock
        window as the state change — a raise anywhere before the single
        persist leaves zero bytes changed on disk (HATS-723/481).
        """
        self.topology.require_state(to_state)
        if force and not reason.strip():
            raise ForceRequiresReasonError()
        if not self._task_path(task_id).exists():
            raise UnknownTaskError(task_id)

        outcomes: list[SubscriberOutcome] = []
        events: list[EdgeEvent] = []
        lock = self._task_lock(task_id)
        try:
            with lock:
                task = self._load(task_id)
                from_state = self._apply_edge(
                    task,
                    to_state,
                    actor=actor,
                    caller_cwd=caller_cwd,
                    force=force,
                    reason=reason,
                    resolution=resolution,
                    final_state=final_state,
                    outcomes=outcomes,
                    events=events,
                )
                self._persist(task)  # the SINGLE persist, always last
        except Timeout as exc:
            raise LockTimeoutError(
                self.tasks_dir / task_id / ".lock", f"transition of {task_id}", self._lock_timeout
            ) from exc
        except Exception:
            if events:  # dispatch began → the refusal stays auditable
                self._finish_record(
                    events[-1], task_id, actor, force, reason, outcomes, result="aborted"
                )
            raise

        event = events[-1]
        ctx = self._ctx_factory(
            event, task, caller_cwd, self.is_epic(task_id), actor, force, reason
        )
        self._dispatcher.run_reactions(event, ctx, outcomes)
        record = self._finish_record(
            event, task_id, actor, force, reason, outcomes, result="persisted"
        )
        return KernelResult(
            task=task,
            transitions=(TaskTransition(task_id, from_state, to_state, reason),),
            journal=(record,),
        )

    def _apply_edge(
        self,
        task: TaskCard,
        to_state: str,
        *,
        actor: str,
        caller_cwd: Path,
        force: bool,
        reason: str,
        resolution: str | None,
        final_state: str | None,
        outcomes: list[SubscriberOutcome],
        events: list[EdgeEvent],
    ) -> str:
        """In-lock edge application: guard/force → mutate → blocking dispatch.

        No lock and no persist of its own — the caller (``transition`` or
        ``transition_ops``) owns both, so a raise anywhere before the single
        persist leaves zero bytes changed on disk (HATS-723/481). Blocking
        subscribers see the card AND any files earlier ops already materialized.
        The edge event is appended to ``events`` BEFORE dispatch, so an abort mid
        dispatch stays auditable (the caller journals ``events[-1]``). Returns
        ``from_state``.
        """
        from_state = task.state
        is_epic = self.is_epic(task.id)
        if force:
            if from_state == to_state:
                raise ValueError(f"Task '{task.id}' is already in state '{to_state}'")
            task.state = to_state
            task.log_work(f"Forced transition {from_state} → {to_state}: {reason}", actor=actor)
        else:
            self.topology.guard(task.id, from_state, to_state)
            task.state = to_state
        task.updated = utc_now()
        # Write-strict on ONLY the fields this transition touches (ADR-0017 §2).
        if resolution is not None:
            self._schema.validate("resolution", resolution)
            task.resolution = resolution
        if final_state is not None:
            self._schema.validate("final_state", final_state)
            task.final_state = final_state

        event = EdgeEvent(from_state, to_state, self._edge_names.get((from_state, to_state), ""))
        events.append(event)
        ctx = self._ctx_factory(event, task, caller_cwd, is_epic, actor, force, reason)
        self._dispatcher.run_blocking(event, ctx, self._delta_applier(task, actor), outcomes)
        return from_state

    def _delta_applier(self, task: TaskCard, actor: str) -> Callable[[Delta], None]:
        """In-memory application of an in-lock delta (work_log + declared-field
        ops) before the single persist — shared by the edge and link paths.
        ``extras: forbid`` refuses an undeclared Set/Append (HATS-1035); a
        declared field's RESULTING value is schema-checked (type/choices/
        validator) AFTER the op — so the model container gate (DeltaFieldError)
        fires first and an Append is judged by the list it yields, not its entry.
        Undeclared names are a no-op (read tolerance); a raise aborts pre-persist."""

        def apply_delta(delta: Delta) -> None:
            for line in delta.work_log:
                task.log_work(line, actor=actor)
            for name, op in delta.fields.items():
                if not self._schema.writable(name):
                    raise ExtrasForbiddenError(name)
                op.apply(task, name)
                self._schema.validate(name, _field_value(task, name))

        return apply_delta

    def transition_ops(
        self,
        task_id: str,
        ops: Sequence[Any],
        *,
        actor: str,
        caller_cwd: Path,
        force: bool = False,
        reason: str = "",
        resolution: str | None = None,
        final_state: str | None = None,
        ack_frozen: bool = False,
    ) -> KernelResult:
        """Ordered composite transition (HATS-1030): a sequence of ops under ONE
        task lock with a SINGLE card persist (K1).

        Op order is execution order; effects of earlier ops are visible to later
        ops' handlers (a state-op's plan-gate sees a file an earlier ``--attach``
        materialized). Any op raising rolls back the WHOLE sequence — the card is
        never persisted and staged files unwind in reverse. Post-lock reactions
        fire per state-op edge after unlock, never nested inside the lock.
        """
        # Local import avoids a load-time cycle (ops imports kernel errors).
        from .ops import OpTxn, StateOp, apply_non_state_op

        if not ops:
            raise ValueError("transition needs at least one operation")
        for op in ops:
            if isinstance(op, StateOp):
                self.topology.require_state(op.to_state)
        if force and not reason.strip():
            raise ForceRequiresReasonError()
        if not self._task_path(task_id).exists():
            raise UnknownTaskError(task_id)

        outcomes: list[SubscriberOutcome] = []
        # Every in-lock-dispatched event in execution order (edges + link events).
        dispatched: list[Event] = []
        transitions: list[TaskTransition] = []
        txn: OpTxn | None = None
        lock = self._task_lock(task_id)
        try:
            with lock:
                task = self._load(task_id)
                txn = OpTxn(
                    task_id=task_id,
                    card=task,
                    card_dir=self.tasks_dir / task_id,
                    caller_cwd=caller_cwd,
                    registry=self.registry,
                    actor=actor,
                    ack_frozen=ack_frozen,
                    dispatch_link=self._link_dispatcher(
                        task, task_id, caller_cwd, actor, force, reason, dispatched, outcomes
                    ),
                    exists=self.target_exists,
                )
                for op in ops:
                    if isinstance(op, StateOp):
                        from_state = self._apply_edge(
                            task,
                            op.to_state,
                            actor=actor,
                            caller_cwd=caller_cwd,
                            force=force,
                            reason=reason,
                            resolution=resolution,
                            final_state=final_state,
                            outcomes=outcomes,
                            events=dispatched,
                        )
                        transitions.append(
                            TaskTransition(task_id, from_state, op.to_state, reason)
                        )
                        txn.results.append(
                            {"op": "state", "from": from_state, "to": op.to_state}
                        )
                    else:
                        apply_non_state_op(txn, op)
                self._persist(task)  # the SINGLE persist, always last
        except Timeout as exc:
            raise LockTimeoutError(
                self.tasks_dir / task_id / ".lock", f"transition of {task_id}", self._lock_timeout
            ) from exc
        except Exception:
            if txn is not None:  # unwind staged files; the card was never persisted
                txn.rollback()
            if dispatched:  # a dispatch began (edge or link) → stay auditable
                self._finish_record(
                    dispatched[-1], task_id, actor, force, reason, outcomes, result="aborted"
                )
            raise

        records: list[DispatchRecord] = []
        for event in dispatched:
            if isinstance(event, EdgeEvent):  # link events have no post-lock phase here
                ctx = self._ctx_factory(
                    event, task, caller_cwd, self.is_epic(task_id), actor, force, reason
                )
                self._dispatcher.run_reactions(event, ctx, outcomes)
            records.append(
                self._finish_record(
                    event, task_id, actor, force, reason, outcomes, result="persisted"
                )
            )
        return KernelResult(
            task=task,
            transitions=tuple(transitions),
            journal=tuple(records),
            ops=tuple(txn.results),
        )

    def _link_dispatcher(
        self,
        task: TaskCard,
        task_id: str,
        caller_cwd: Path,
        actor: str,
        force: bool,
        reason: str,
        dispatched: list[Event],
        outcomes: list[SubscriberOutcome],
    ) -> Callable[[str, str, bool], None]:
        """Build the in-lock link/unlink dispatch hook for a composite txn.

        Fires ``link:<kind>``/``unlink:<kind>`` ONLY when a declared handler
        subscribes it — a kind without handlers dispatches nothing (zero
        behavior change on the packaged default). An in-lock abort propagates
        out of the op, rolling the whole txn back before persist."""
        apply_delta = self._delta_applier(task, actor)

        def dispatch_link(kind: str, target: str, removed: bool) -> None:
            event = LinkEvent(kind=kind, target=target, removed=removed)
            if not self._dispatcher.subscribers_for(event.key, Phase.IN_LOCK):
                return
            dispatched.append(event)
            ctx = self._ctx_factory(
                event, task, caller_cwd, self.is_epic(task_id), actor, force, reason
            )
            self._dispatcher.run_blocking(event, ctx, apply_delta, outcomes)

        return dispatch_link

    def log_work(self, task_id: str, message: str, *, actor: str = "") -> TaskCard:
        """Append a work_log entry (anchor field — CLI-only, transactional)."""
        lock = self._task_lock(task_id)
        try:
            with lock:
                task = self._load(task_id)
                task.log_work(message, actor=actor)
                task.updated = utc_now()
                self._persist(task)
        except Timeout as exc:
            raise LockTimeoutError(
                self.tasks_dir / task_id / ".lock", f"log_work on {task_id}", self._lock_timeout
            ) from exc
        return task

    def set_parent(
        self, task_id: str, parent_task: str, *, actor: str, caller_cwd: Path
    ) -> KernelResult:
        """Reparent a task. Gaining a child epicifies the new parent — a
        first-class dispatcher event, not an FSM edge (HATS-977/979)."""
        if parent_task == task_id:
            raise ValueError(f"Task '{task_id}' cannot be its own parent")
        lock = self._task_lock(task_id)
        try:
            with lock:
                task = self._load(task_id)
                task.parent_task = parent_task
                task.updated = utc_now()
                self._persist(task)
        except Timeout as exc:
            raise LockTimeoutError(
                self.tasks_dir / task_id / ".lock", f"set_parent on {task_id}", self._lock_timeout
            ) from exc
        journal = self._dispatch_epicify(parent_task, task_id, actor=actor, caller_cwd=caller_cwd)
        return KernelResult(task=task, journal=journal)

    def publish(
        self,
        event: PreDestroyEvent,
        *,
        actor: str,
        caller_cwd: Path,
        force: bool = False,
        reason: str = "",
    ) -> tuple[DispatchRecord, ...]:
        """Extension-facing blocking dispatch for pre-destroy events.

        Runs IN_LOCK subscriptions inside the publisher's own operation
        window (no task lock is taken here); an abort propagates so the
        extension cancels the destructive operation. Deltas are journal-only.
        """
        task = self._load(event.task_id)
        is_epic = self.is_epic(event.task_id)
        outcomes: list[SubscriberOutcome] = []
        ctx = self._ctx_factory(event, task, caller_cwd, is_epic, actor, force, reason)
        try:
            self._dispatcher.run_blocking(event, ctx, lambda delta: None, outcomes)
        except Exception:
            self._finish_record(
                event, event.task_id, actor, force, reason, outcomes, result="aborted"
            )
            raise
        # "persisted" here means the publisher's operation may proceed.
        return (
            self._finish_record(
                event, event.task_id, actor, force, reason, outcomes, result="persisted"
            ),
        )

    def apply_mirror(
        self, event: Any, *, actor: str, caller_cwd: Path
    ) -> DispatchRecord | None:
        """Run a link mirror reaction on the TARGET card in a FRESH lock window
        (ADR-0017 §2/R4): sequential, never nested, and fail-soft — a reaction
        failure is journaled and swallowed (the origin already persisted, so the
        mirror can never abort it). No subscribers / a dangling target -> a no-op
        (zero behavior change for a backlog with no mirror kinds).

        The handler mutates the REAL target card here (its own repair window — it
        is the target's executor, not an observer), so the copy-guard the owning
        transition uses does not apply; the reverse edge is convergent/idempotent.
        """
        subs = self._dispatcher.subscribers_for(event.key, Phase.POST_LOCK)
        if not subs or not self._task_path(event.target).exists():
            return None
        outcomes: list[SubscriberOutcome] = []
        lock = self._task_lock(event.target)
        try:
            with lock:
                task = self._load(event.target)
                changed = False
                for sub in subs:
                    ctx = DispatchContext(
                        event=event,
                        task=task,  # real card: the mirror repair window
                        caller_cwd=caller_cwd,
                        is_epic=self.is_epic(event.target),
                        actor=actor,
                    )
                    delta = sub.on_event(ctx)
                    if delta is not None:
                        changed = True
                        outcomes.append(
                            SubscriberOutcome(sub.name, Phase.POST_LOCK, "delta", delta=delta)
                        )
                    else:
                        outcomes.append(SubscriberOutcome(sub.name, Phase.POST_LOCK, "ok"))
                if changed:
                    task.updated = utc_now()
                    self._persist(task)
        except Timeout:
            return self._finish_record(
                event, event.target, actor, False, "", outcomes, result="aborted"
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft: never reaches the origin
            outcomes.append(SubscriberOutcome("mirror", Phase.POST_LOCK, "error", reason=repr(exc)))
            return self._finish_record(
                event, event.target, actor, False, "", outcomes, result="aborted"
            )
        return self._finish_record(
            event, event.target, actor, False, "", outcomes, result="persisted"
        )

    # ----- internals ----------------------------------------------------------

    def _ctx_factory(
        self,
        event: Event,
        task: TaskCard,
        caller_cwd: Path,
        is_epic: bool,
        actor: str,
        force: bool,
        reason: str,
    ):
        def make_ctx() -> DispatchContext:
            return DispatchContext(
                event=event,
                task=task.model_copy(deep=True),  # immutable-by-copy: no store handle
                caller_cwd=caller_cwd,
                is_epic=is_epic,
                actor=actor,
                force=force,
                reason=reason,
            )

        return make_ctx

    def _dispatch_epicify(
        self, parent_task: str, child_id: str, *, actor: str, caller_cwd: Path
    ) -> tuple[DispatchRecord, ...]:
        """Reaction-phase dispatch of the epicify event (nothing to abort —
        the child already exists; handlers reconcile, idempotently)."""
        if not parent_task:
            return ()
        parent = self.get(parent_task)
        if parent is None:
            return ()  # dangling parent ref: nothing to reconcile against
        event = EpicifyEvent(epic_id=parent_task, child_id=child_id)
        outcomes: list[SubscriberOutcome] = []
        ctx = self._ctx_factory(
            event, parent, caller_cwd, self.is_epic(parent_task), actor, False, ""
        )
        self._dispatcher.run_reactions(event, ctx, outcomes)
        return (
            self._finish_record(event, parent_task, actor, False, "", outcomes, result="persisted"),
        )
