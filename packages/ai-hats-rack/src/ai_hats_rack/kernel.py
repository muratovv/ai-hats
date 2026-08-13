"""The rack kernel: transactional transition engine over task.yaml (HATS-1020).

transition = FileLock → FSM-guard → in-memory mutation → two-phase dispatch →
SINGLE persist of task.yaml, last. A bare kernel (no subscribers) is a pure
FSM — the explicit contract inherited from HATS-866/AC4.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    from filelock import FileLock

from .cardschema import CardSchema, ExtrasForbiddenError, RequiredFieldError, default_card_schema
from .dispatch import (
    Delta,
    DispatchContext,
    Dispatcher,
    DispatchRecord,
    JournalSink,
    Phase,
    Set,
    Subscriber,
    SubscriberOutcome,
)
from .errors import RackError
from .events import (
    EdgeEvent,
    EpicifyEvent,
    Event,
    FieldsEvent,
    LinkEvent,
    PreDestroyEvent,
    event_detail,
)
from .fsm import Topology, load_topology
from .ids import prefix_of
from .models import LINK_STORAGE_FIELDS, TaskCard, utc_now
from .registry import LinksRegistry, load_registry

# Single loud-fail timeout pattern (HATS-936 / epic §2.2 rule 5): a wait this
# long on a sub-second fs op means a stuck holder, not real contention.
LOCK_TIMEOUT = 30.0


def _field_value(task: TaskCard, name: str) -> Any:
    """Current value of a card field: a TaskCard column or an extras-resident key
    (a custom backlog's declared field that is not a kernel-anchor column)."""
    return getattr(task, name) if name in TaskCard._KNOWN_FIELDS else task.extras.get(name)


def _reverse_field_gate(kind: Any) -> re.Pattern[str]:
    """Cheap text gate: does this card plausibly declare ``kind``'s field?

    Skips the parse for the majority of cards that omit the field. NOT a strict
    superset — a quoted key, a space before the colon, a merge anchor or a
    top-level flow map all read fine and gate out. That matches what
    ``children_of`` has always done for ``parent_task``; the emitter never
    writes those shapes.
    """
    field = re.escape(kind.name)
    if kind.name in LINK_STORAGE_FIELDS:
        return re.compile(rf"^{field}\s*:", re.MULTILINE)  # emitted at column 0
    return re.compile(rf"^\s*{field}\s*:", re.MULTILINE)  # nested under `links:`


def _scalar_reader(name: str) -> re.Pattern[str]:
    """Reads a scalar link field off the card text, no YAML parse.

    Captures the whole value so an id with spaces survives (``ids.py`` blesses
    free-form tails); ``_scalar_target`` decides whether it is safe to trust.
    """
    return re.compile(rf"^{re.escape(name)}[ \t]*:[ \t]*(.*)$", re.MULTILINE)


#: YAML that a flat regex read cannot be trusted with — defer to the parser.
_YAML_SIGILS = ("&", "*", "!", "|", ">", "{", "[", "#", '"', "'")


def _scalar_target(text: str, reader: re.Pattern[str]) -> str | None:
    """The scalar's value: an id, ``""`` for a definite no-edge, or ``None``
    when the text is a shape a flat read cannot be trusted with — parse then.

    ``""`` matters as much as the id: an unparented card emits ``parent_task:
    ''``, so treating empty as "cannot tell" sends the whole catalog through
    the YAML parser and undoes the point of the fast path.
    """
    found = False
    raw = ""
    for hit in reader.finditer(text):  # last key wins, as YAML does
        raw = hit.group(1).strip()
        found = True
    if not found:
        return None
    if raw in {"", "''", '""', "null", "~"}:
        return ""
    if raw.startswith(_YAML_SIGILS):
        return None
    return raw


class UnknownTaskError(RackError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' not found")


class TaskExistsError(RackError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' already exists")


class UnroutableIdError(RackError):
    """An explicit create id whose prefix is not this catalog's, so no read or
    mutate verb could ever route back to the card (HATS-1283)."""

    def __init__(self, task_id: str, prefix: str) -> None:
        self.task_id = task_id
        self.prefix = prefix
        super().__init__(
            f"Id '{task_id}' does not route to this backlog: expected "
            f"'{prefix}-<number>'. A card minted here under another prefix is "
            "unreachable by every other verb."
        )


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
        # Per-instance reverse-scan memo: a walk shares one Kernel, so without
        # it every node re-swept the catalog per derived kind (HATS-1208).
        self._reverse_idx: dict[str, dict[str, list[str]]] = {}

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
        self._reverse_idx.clear()  # a write invalidates the reverse memo
        self._task_path(task.id).parent.mkdir(parents=True, exist_ok=True)
        # The emit gate (schema when-set fields dropped when empty) runs at the
        # single persist, hung off the schema — TaskCard.to_dict stays untouched.
        task.save(self._task_path(task.id), transform=self._schema.emit_filter)

    def _task_lock(self, task_id: str) -> FileLock:
        from filelock import FileLock

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
            except Exception:  # noqa: S112
                # silent-ok: a broken neighbour must not sink the catalog read
                continue
            if self.registry.parent_of(card) == task_id:
                out.append(card_path.parent.name)
        return out

    def reverse_links_of(self, stored_kind_name: str, task_id: str) -> list[str]:
        """Ids of cards linking to ``task_id`` via ``stored_kind_name``.

        Answers only for kinds something reads in reverse — one a derived kind
        inverts, plus the hierarchy kind. Any other stored kind (``related``,
        ``see_also``, …) is nobody's reverse view and comes back empty.

        Backed by a memo this kernel drops on its own writes, so treat the
        result as a snapshot: a card written through another handle (``link``,
        ``DocStore``) after the first call is not reflected.
        """
        if not self.tasks_dir.exists():
            return []
        kind = self.registry.get(stored_kind_name)
        if kind is None or kind.derived:
            return []
        return list(self._reverse_indexes().get(kind.name, {}).get(task_id, ()))

    def _reverse_indexes(self) -> dict[str, dict[str, list[str]]]:
        """``{kind: {target_id: [source_id, ...]}}`` for every kind some derived
        kind inverts, built in ONE catalog pass and memoized per instance.

        One pass, not one per kind per node: a two-derived-kind registry asking
        per target re-read the whole catalog twice for every node of a walk
        (HATS-1208). ``children_of``/``is_epic`` deliberately stay off this —
        their contract is a fresh count per dispatch.
        """
        if self._reverse_idx:
            return self._reverse_idx

        # Every kind a derived kind inverts, PLUS the hierarchy kind — a derived
        # kind is not obliged to name its inverse back, and dropping the parent
        # edge here empties the children view (HATS-1208 review).
        wanted: dict[str, Any] = {}
        for dk in self.registry.derived_kinds:
            inverse = self.registry.get(dk.inverse) if dk.inverse else None
            if inverse is not None:
                wanted[dk.inverse] = inverse
        hierarchy = self.registry.hierarchy_kind
        if hierarchy is not None:
            wanted.setdefault(hierarchy.name, hierarchy)

        indexes: dict[str, dict[str, list[str]]] = {name: {} for name in wanted}
        if not wanted:
            return indexes
        gates = {name: _reverse_field_gate(kind) for name, kind in wanted.items()}
        # A dedicated scalar field is usually readable straight off the text, so
        # the hierarchy kind keeps children_of's parse-free cost; anything the
        # flat read cannot be trusted with falls through to the parser.
        scalars = {
            name: _scalar_reader(name)
            for name, kind in wanted.items()
            if name in LINK_STORAGE_FIELDS and kind.arity == "one"
        }

        for card_path in sorted(self.tasks_dir.glob("*/task.yaml")):
            try:
                text = card_path.read_text(encoding="utf-8")
            except OSError:
                continue
            present = [n for n, gate in gates.items() if gate.search(text)]
            if not present:
                continue
            source = card_path.parent.name
            card: TaskCard | None = None
            for name in present:
                targets: Any = None
                if name in scalars:
                    scalar = _scalar_target(text, scalars[name])
                    if scalar is not None:
                        targets = (scalar,) if scalar else ()
                if targets is None:
                    if card is None:
                        try:
                            card = TaskCard.from_yaml(card_path)
                        except Exception:
                            # silent-ok: a broken neighbour must not sink the reverse index
                            break
                    value = (
                        getattr(card, name, None)
                        if name in LINK_STORAGE_FIELDS
                        else card.links.get(name, ())
                    )
                    targets = (value,) if isinstance(value, str) else value
                if not isinstance(targets, (list, tuple)):
                    continue
                for target in targets:
                    if isinstance(target, str) and target:
                        indexes[name].setdefault(target, []).append(source)

        self._reverse_idx = indexes
        return indexes

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
        fields: Mapping[str, Any] | None = None,
        links: Mapping[str, Sequence[str]] | None = None,
    ) -> KernelResult:
        """Create a card. Id allocation + reserve is atomic under the
        directory-scoped alloc lock (HATS-936); timeout is a loud failure.

        ``title`` is the only required input (ADR-0017 §1); the schema fields
        (``None`` sentinels) resolve to their declared defaults and are validated
        write-strict — a bad choice/type/required field is a typed refusal.
        ``fields`` is the generic field mapping (HATS-1036): any declared field by
        name (a custom backlog's required ``hypothesis`` etc.), merged over the
        named tasks kwargs so both the tasks verb and per-backlog groups share one
        create path; a name absent from the routed schema is ignored.
        ``links`` is its link-side twin (HATS-1596): ``{kind: [target, …]}`` for any
        declared kind, applied through the same link op as the named
        ``parent_task``/``depends_on``."""  # comment-length: allow — the two generic channels are the contract
        if not title.strip():
            raise RequiredFieldError("title", "a task requires a non-empty title")
        if task_id is not None and prefix_of(task_id) != self.prefix:
            raise UnroutableIdError(task_id, self.prefix)
        provided: dict[str, Any] = {
            "description": description,
            "priority": priority,
            "role": role,
            "reviewer": reviewer,
            "tags": tags,
        }
        if fields:
            provided.update(fields)
        resolved = self._schema.resolve_create(provided)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        # Not a `<prefix>-N` card dir, so scans ignore it.
        alloc_lock_path = self.tasks_dir / ".alloc.lock"
        from filelock import FileLock, Timeout

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
                    created=now,
                    updated=now,
                    **resolved,
                )
                # HATS-1327/1333: pre-persist, so a refused link writes nothing.
                self._apply_declared_links(
                    task,
                    self._declared_links(parent_task, depends_on, links),
                    actor=actor,
                    caller_cwd=caller_cwd,
                )
                self._persist(task)
        except Timeout as exc:
            raise LockTimeoutError(
                alloc_lock_path, "task-id allocation", self._lock_timeout
            ) from exc
        journal = self._dispatch_epicify(parent_task, task.id, actor=actor, caller_cwd=caller_cwd)
        return KernelResult(task=task, journal=journal)

    def _apply_declared_links(
        self, task: TaskCard, links: Sequence[tuple[str, str]], *, actor: str, caller_cwd: Path
    ) -> None:
        """Apply declared ``(kind, target)`` links through the transition link op.

        The point of HATS-1327/1333: one write path for a link, so a card cannot
        hold an edge that `transition --link` would have refused. The card is
        mutated in memory, so a refusal aborts before any write.
        ``dispatch_link`` stays None — these paths never fired link events, and
        this is a guard change, not an event change.
        """
        if not links:
            return
        from .ops import LinkOp, OpTxn, apply_non_state_op

        txn = OpTxn(
            task_id=task.id,
            card=task,
            card_dir=self.tasks_dir / task.id,
            caller_cwd=caller_cwd,
            registry=self.registry,
            actor=actor,
            # The card being created is not on disk yet, but it exists for link
            # validation — otherwise a self-reference reads as "target missing"
            # instead of the precise SelfLinkError.
            exists=lambda tid, targets: tid == task.id or self.target_exists(tid, targets),
        )
        for kind, target in links:
            apply_non_state_op(txn, LinkOp(kind=kind, target=target))

    @staticmethod
    def _declared_links(
        parent_task: str,
        depends_on: Sequence[str],
        extra: Mapping[str, Sequence[str]] | None = None,
    ) -> list[tuple[str, str]]:
        """An empty ``parent_task`` means "no parent" — never an existence check."""
        links: list[tuple[str, str]] = [("parent_task", parent_task)] if parent_task else []
        links += [("depends_on", t) for t in depends_on]
        for kind, targets in (extra or {}).items():
            links += [(kind, t) for t in targets if t]
        return links

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

        Delegates to :meth:`transition_ops` via a single :class:`~ai_hats_rack.ops.StateOp`.
        ``force`` relaxes ONLY the FSM arrow (never subscriber safety) and
        requires a reason. ``resolution`` / ``final_state`` ride the same lock
        window as the state change — a raise anywhere before the single
        persist leaves zero bytes changed on disk (HATS-723/481).
        """
        from .ops import StateOp

        return self.transition_ops(
            task_id,
            [StateOp(to_state)],
            actor=actor,
            caller_cwd=caller_cwd,
            force=force,
            reason=reason,
            resolution=resolution,
            final_state=final_state,
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
        lock_expires_at: float | None = None,
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
        ctx = self._ctx_factory(
            event, task, caller_cwd, is_epic, actor, force, reason, lock_expires_at
        )
        self._dispatcher.run_blocking(event, ctx, self._delta_applier(task, actor), outcomes)
        return from_state

    def _resolve_state_target(self, token: str, current: str) -> str:
        """Resolve a transition target (HATS-1036 sugar): a state name passes
        through; a declared edge NAME resolves to its target, preferring the edge
        leaving ``current``. A name whose edge does not start at ``current`` still
        yields that name's target, so the FSM guard raises the usual
        InvalidTransitionError with the legal edges; a token that is neither is
        returned unchanged (require_state/guard then raises UnknownStateError)."""
        if token in self.topology.states:
            return token
        candidates = [(frm, to) for (frm, to), name in self._edge_names.items() if name == token]
        if not candidates:
            return token
        for frm, to in candidates:
            if frm == current:
                return to
        return candidates[0][1]

    def _gate_values(self, task: TaskCard) -> dict[str, Any]:
        return {f.name: _field_value(task, f.name) for f in self._schema.fields}

    def _check_state_gates(
        self, task: TaskCard, entered: Iterable[str], before: Mapping[str, Any]
    ) -> None:
        """Run the declared state-conditional gates against the RESULTING card
        (HATS-1275). Called by BOTH transition paths immediately before their
        single persist — a kwarg-side check would leave the ``--set`` path open."""
        self._schema.check_state_gates(
            entered=frozenset(entered), before=before, after=self._gate_values(task)
        )

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
        from filelock import Timeout

        # Local import avoids a load-time cycle (ops imports kernel errors).
        from .ops import FieldsOp, OpTxn, StateOp, apply_non_state_op

        if not ops:
            raise ValueError("transition needs at least one operation")
        # An edge-NAME target (HATS-1036 sugar) resolves to a real state under the
        # lock (current state known there); pre-validate only plain-state tokens.
        edge_name_targets = frozenset(self._edge_names.values())
        for op in ops:
            if isinstance(op, StateOp) and op.to_state not in edge_name_targets:
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
        old_parent = ""
        new_parent = ""
        lock = self._task_lock(task_id)
        try:
            with lock:
                # HATS-1603: in-lock work inherits this ceiling, so a nested
                # budget (a wt hook under the wt lifecycle lock) computes from
                # who called instead of trusting its own lock alone.
                lock_expires_at = time.monotonic() + self._lock_timeout
                task = self._load(task_id)
                old_parent = task.parent_task
                before = self._gate_values(task)
                entered: list[str] = []
                txn = OpTxn(
                    task_id=task_id,
                    card=task,
                    card_dir=self.tasks_dir / task_id,
                    caller_cwd=caller_cwd,
                    registry=self.registry,
                    actor=actor,
                    ack_frozen=ack_frozen,
                    dispatched=dispatched,
                    dispatch_link=self._link_dispatcher(
                        task,
                        task_id,
                        caller_cwd,
                        actor,
                        force,
                        reason,
                        dispatched,
                        outcomes,
                        lock_expires_at,
                    ),
                    exists=self.target_exists,
                )
                for op in ops:
                    if isinstance(op, StateOp):
                        to_state = self._resolve_state_target(op.to_state, task.state)
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
                            events=dispatched,
                            lock_expires_at=lock_expires_at,
                        )
                        transitions.append(TaskTransition(task_id, from_state, to_state, reason))
                        entered.append(to_state)
                        txn.results.append({"op": "state", "from": from_state, "to": to_state})
                    elif isinstance(op, FieldsOp):
                        # Declared-field ops ride the same lock/persist, schema-
                        # gated via _delta_applier — an extension-owned field write
                        # (validation_log/votes) atomic with any StateOp above.
                        self._delta_applier(task, actor)(Delta(fields=op.fields))
                        for name, field_op in op.fields.items():
                            op_name = "set" if isinstance(field_op, Set) else "append"
                            val = (
                                str(field_op.value)
                                if isinstance(field_op, Set)
                                else str(field_op.entry)
                            )
                            dispatched.append(FieldsEvent(field=name, op=op_name, value=val))
                        txn.results.append({"op": "fields", "names": sorted(op.fields)})
                    else:
                        apply_non_state_op(txn, op)
                self._check_state_gates(task, entered, before)
                self._persist(task)  # the SINGLE persist, always last
                new_parent = task.parent_task
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
                # No lock_expires_at: the lock is released above, so a reaction
                # is bounded by nothing of ours (HATS-1603).
                ctx = self._ctx_factory(
                    event, task, caller_cwd, self.is_epic(task_id), actor, force, reason
                )
                self._dispatcher.run_reactions(event, ctx, outcomes)
            records.append(
                self._finish_record(
                    event, task_id, actor, force, reason, outcomes, result="persisted"
                )
            )
        if new_parent and new_parent != old_parent:
            epicify_records = self._dispatch_epicify(
                new_parent, task_id, actor=actor, caller_cwd=caller_cwd
            )
            records.extend(epicify_records)
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
        lock_expires_at: float | None = None,
    ) -> Callable[[str, str, bool], None]:
        """Build the in-lock link/unlink dispatch hook for a composite txn.

        Fires ``link:<kind>``/``unlink:<kind>`` ONLY when a declared handler
        subscribes it — a kind without handlers dispatches nothing (zero
        behavior change on the packaged default). An in-lock abort propagates
        out of the op, rolling the whole txn back before persist."""
        apply_delta = self._delta_applier(task, actor)

        def dispatch_link(kind: str, target: str, removed: bool) -> None:
            event = LinkEvent(kind=kind, target=target, removed=removed)
            dispatched.append(event)
            if not self._dispatcher.subscribers_for(event.key, Phase.IN_LOCK):
                return
            ctx = self._ctx_factory(
                event,
                task,
                caller_cwd,
                self.is_epic(task_id),
                actor,
                force,
                reason,
                lock_expires_at,
            )
            self._dispatcher.run_blocking(event, ctx, apply_delta, outcomes)

        return dispatch_link

    def log_work(self, task_id: str, message: str, *, actor: str = "") -> TaskCard:
        """Append a work_log entry (anchor field — CLI-only, transactional)."""
        from filelock import Timeout

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
        from filelock import Timeout

        lock = self._task_lock(task_id)
        try:
            with lock:
                task = self._load(task_id)
                # HATS-1333: same link op as create and `transition --link`.
                self._apply_declared_links(
                    task,
                    self._declared_links(parent_task, ()),
                    actor=actor,
                    caller_cwd=caller_cwd,
                )
                if not parent_task:  # clearing is a plain field write, no target
                    task.parent_task = ""
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
        lock_expires_at: float | None = None,
    ) -> tuple[DispatchRecord, ...]:
        """Extension-facing blocking dispatch for pre-destroy events.

        Runs IN_LOCK subscriptions inside the publisher's own operation
        window (no task lock is taken here); an abort propagates so the
        extension cancels the destructive operation. Deltas are journal-only.

        ``lock_expires_at`` is the publisher's ceiling, forwarded so a
        subscriber is bounded by whatever lock the publisher holds — it knows
        that lock, this call cannot infer it (HATS-1603).
        """
        task = self._load(event.task_id)
        is_epic = self.is_epic(event.task_id)
        outcomes: list[SubscriberOutcome] = []
        ctx = self._ctx_factory(
            event, task, caller_cwd, is_epic, actor, force, reason, lock_expires_at
        )
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

    def apply_mirror(self, event: Any, *, actor: str, caller_cwd: Path) -> DispatchRecord | None:
        """Run a link mirror reaction on the TARGET card in a FRESH lock window
        (ADR-0017 §2/R4): sequential, never nested, and fail-soft — a reaction
        failure is journaled and swallowed (the origin already persisted, so the
        mirror can never abort it). No subscribers / a dangling target -> a no-op
        (zero behavior change for a backlog with no mirror kinds).

        The handler mutates the REAL target card here (its own repair window — it
        is the target's executor, not an observer), so the copy-guard the owning
        transition uses does not apply; the reverse edge is convergent/idempotent.
        """
        from filelock import Timeout

        subs = self._dispatcher.subscribers_for(event.key, Phase.POST_LOCK)
        if not subs or not self._task_path(event.target).exists():
            return None
        outcomes: list[SubscriberOutcome] = []
        lock = self._task_lock(event.target)
        try:
            with lock:
                task = self._load(event.target)
                changed = False
                apply_delta = self._delta_applier(task, actor)
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
                        apply_delta(delta)
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
        lock_expires_at: float | None = None,
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
                lock_expires_at=lock_expires_at,
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
