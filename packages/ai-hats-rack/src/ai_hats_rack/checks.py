"""The FSM half of the binding channel: the rack owns its own point names.

ADR-0019 D11. An integrator composes a role and hands over already-resolved
declarations; what ``edge:<from>--<to>`` *means*, whether this instance's
topology has that edge, and which subscriptions follow are decided here — by the
only party that holds the topology the kernel is running.

The rack does not execute: ``subprocess`` is forbidden in this package by an
AST-level import pin. So the declaration travels as data and the executor
travels as a port (:class:`CheckPort`), probed with ``getattr`` rather than
assumed, because a port older than this Protocol must not raise
``AttributeError`` inside the task lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from .definition import BacklogDefinition
from .dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from .fsm import Topology, all_edges
from .selectors import ANY, Edge, Selector, parse_selector
from .kernel import LOCK_TIMEOUT

#: Reserved "hook" slot of the in-lock ladder — after the plan-gate, before the
#: ownership claim, so a refusal leaves neither ownership nor a worktree.
CHECK_PRIORITY = 15

#: Per-check wall-clock budget, strictly below the lock timeout so a hung check
#: is bounded by ITS deadline and not by the lock (ADR-0020 D2). The rack owns
#: ``LOCK_TIMEOUT``, so it owns the budget too and ships it across the port —
#: a constant kept on both sides is a constant that drifts.
EDGE_CHECK_TIMEOUT_S: float = 20.0

if EDGE_CHECK_TIMEOUT_S >= LOCK_TIMEOUT:  # pragma: no cover — explicit raise survives -O
    raise RuntimeError(
        f"EDGE_CHECK_TIMEOUT_S ({EDGE_CHECK_TIMEOUT_S}) must be < LOCK_TIMEOUT ({LOCK_TIMEOUT})"
    )


#: What a carried row IS (HATS-1682). A ``CHECK_ROW`` spawns a script when its
#: point fires; a ``CONSENT_ROW`` spawns nothing and only declares that the
#: supervisor is asked there. Both are addressed and judged against the topology
#: alike — the second kind is carried for exactly that, and never run.
# comment-length: allow — which kind runs is the contract
CHECK_ROW = "check"
CONSENT_ROW = "consent"


@dataclass(frozen=True)
class CheckDeclaration:
    """One carried row. ``handle`` is opaque — the rack never looks inside.

    Since HATS-1545 the carrier does not know this package's grammar: it hands
    over ``path`` (where the row sat under ``apps.rack`` — the backlog it names)
    and ``cargo`` (every key ai-hats does not own), and THIS side decides what
    they mean. ``label`` is whatever the carrier wants a refusal to say about
    the row; the rack quotes it and never parses it. ``kind`` defaults to
    ``CHECK_ROW``, so a carrier older than HATS-1682 declares what it always did.
    """

    path: tuple[str, ...]
    at: tuple[str, ...]
    cargo: Mapping[str, Any]
    on_error: str
    label: str
    handle: Any
    kind: str = CHECK_ROW

    def points(self) -> tuple[str, ...]:
        """The point names this row binds. The carrier guarantees it is non-empty
        — that a row names SOMETHING is app-agnostic; what the names mean is ours."""
        return tuple(self.at)


@dataclass(frozen=True)
class CheckRequest:
    """One firing, handed back to the carrier's executor.

    ``event`` is the point being fired. The carrier needs it to keep one log per
    (task, point, row): a row may bind several points, and ``run_hook`` truncates
    the log it is handed, so a name without it lets the second firing wipe the
    first one's file (HATS-1137).
    """

    declaration: CheckDeclaration
    task_id: str
    force: bool
    timeout: float
    event: str = ""
    #: ``time.monotonic()`` instant the enclosing task lock runs out, None when
    #: none is declared. Untyped for the same reason as on ``DispatchContext``:
    #: the rack ships a number and the executor mints the budget type from it,
    #: so a check is bounded by the lock it runs in (HATS-1603).
    lock_expires_at: float | None = None


@dataclass(frozen=True)
class CheckOutcome:
    """What the executor reports back. ``downgradable`` is the ADR-0020 D2
    distinction: a check that *refused* is never downgraded by ``on_error:
    warn``; only a check whose substrate broke is."""

    ok: bool
    reason: str = ""
    downgradable: bool = False


@runtime_checkable
class CheckPort(Protocol):
    """The integrator side: where declarations come from, and who runs one."""

    def check_declarations(self) -> Sequence[CheckDeclaration]: ...

    def run_check(self, request: CheckRequest) -> CheckOutcome: ...


#: How an integrator supplies the executor for ONE catalog. The rack builds the
#: subscriber itself (:func:`check_subscriber`); this is the whole of what the
#: integrator contributes, because it is the whole of what the rack cannot do.
CheckPortFactory = Callable[[Path], CheckPort]


#: What a report says about one point of one carried row (HATS-1584). ``dead``
#: is the miss no topology of this project answers — the one HATS-1578 refuses.
ARMED = "armed"
FOREIGN = "foreign"
DEAD = "dead"
UNADDRESSED = "unaddressed"


@dataclass(frozen=True)
class BindingStatus:
    """One line of the binding report: a row's point, judged.

    ``detail`` carries this package's own words for an unhappy status and is
    empty for a happy one. What a selector means is the rack's question, so the
    sentence explaining a miss is written here rather than by whoever prints it.

    ``selector`` and not ``point`` since HATS-1719: the word had to change with
    the grammar, because ``review->done`` denotes one event and ``->done``
    denotes eight — "point" was honest only for the first (design.md §1.1).
    """

    status: str
    backlog: str
    selector: str
    label: str
    on_error: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "backlog": self.backlog,
            "selector": self.selector,
            "binding": self.label,
            "on_error": self.on_error,
            "detail": self.detail,
        }


def classify_bindings(
    declarations: Sequence[CheckDeclaration],
    topologies: Mapping[str, Topology],
) -> tuple[BindingStatus, ...]:
    """Every carried row's points, judged against EVERY mounted topology.

    A subscriber holds one topology, so from where it stands a sibling backlog's
    edge and a typo are the same fact — a name it does not have (ADR-0019 D11
    clause 2). Given all of them the two separate: ``foreign`` is the skip
    HATS-1545 R10 made legal, ``dead`` is a gate that fires nowhere, ever.

    ``topologies`` is keyed by every selector a backlog answers to — its name
    AND its ``cli_alias``, since either addresses it (ADR-0017 §3); keying on
    one spelling would read an aliased row as unaddressed.
    """  # comment-length: allow — which miss is which is the whole contract
    edges_of = {name: tuple(all_edges(t)) for name, t in topologies.items()}
    mounted = sorted(edges_of)
    rows: list[BindingStatus] = []
    for row in declarations:
        address = ".".join(row.path)
        if len(row.path) != 1 or row.path[0] not in edges_of:
            rows.append(
                BindingStatus(
                    UNADDRESSED,
                    address,
                    "",
                    row.label,
                    row.on_error,
                    unaddressed_reason(row, mounted),
                )
            )
            continue
        here = edges_of[row.path[0]]
        for point in row.points():
            selector = parse_selector(point)
            status = (
                ARMED
                if _hits(selector, here)
                else FOREIGN
                if any(_hits(selector, edges) for edges in edges_of.values())
                else DEAD
            )
            detail = dead_selector_reason(row, point, mounted) if status == DEAD else ""
            rows.append(BindingStatus(status, address, point, row.label, row.on_error, detail))
    return tuple(rows)


def _hits(selector: Selector | None, edges: Sequence[Edge]) -> bool:
    """Whether ``selector`` denotes at least one event of this edge set.

    ``None`` — a name holding no arrow — hits nothing: it is addressed to some
    other application's grammar, and from here that is the same fact as a typo.
    """
    return selector is not None and any(selector.matches(edge) for edge in edges)


def unaddressed_reason(row: CheckDeclaration, mounted: Sequence[str]) -> str:
    """Why a row addresses no mounted backlog — ONE spelling for both readers.

    The subscriber raises it inside the lock and a report prints it outside one.
    Two copies of a recipe drift, and here the recipe is the whole value.
    """
    if len(row.path) != 1:
        return (
            f"checks: {row.label} sits at apps.rack{''.join('.' + p for p in row.path)}, but a "
            f"rack row is declared one level down, under the backlog it gates "
            f"(apps.rack.<backlog>) — this one names "
            f"{'no backlog' if not row.path else 'a deeper path'}"
        )
    name = row.path[0]
    return (
        f"checks: {row.label} is declared under apps.rack.{name}, but no backlog of this "
        f"project answers to {name!r} (mounted: {', '.join(mounted)}) — "
        f"a gate on a backlog that does not exist would never fire. "
        f"Give the backlog it means `cli_alias: {name}` in its backlog.yaml, so it "
        f"answers to both selectors — that is the fix when the row ships with a role "
        f"you do not own. Otherwise re-address the row to one of: "
        f"{', '.join('apps.rack.' + s for s in mounted)}."
    )


def dead_selector_reason(row: CheckDeclaration, point: str, mounted: Sequence[str]) -> str:
    """Why a point fires nowhere. Said with the mounted roster, because that is
    what makes it a typo rather than a row aimed at a backlog of some other
    project — the distinction only a holder of every topology can draw."""
    what = "the gate" if row.kind == CHECK_ROW else "the consent question"
    return (
        f"checks: {row.label} binds {point!r} under apps.rack.{'.'.join(row.path)}, but no "
        f"topology mounted in this project matches it (backlogs: {', '.join(mounted)}) — "
        f"{what} can never fire, on that edge or any other. A rack selector is an "
        f"arrow between the state names of the backlog it gates: `review->done` for "
        f"exactly that edge, `->done` for every road into the state."
    )


class CheckSubscriber:
    """Runs the carried bindings whose point is an edge of THIS topology.

    Subscribes to the whole state product rather than to the declared set: the
    declarations are resolved lazily, on the first event, so that neither
    ``rack ls`` nor ``rack context`` pays a composition for a read (the
    fail-closed-on-discovery shape is what bricked reads in HATS-1538).
    """

    name = "checks"

    def __init__(
        self,
        port: Any,
        *,
        topology: Topology,
        backlog: str | Sequence[str],
        known_backlogs: Sequence[str] = (),
        priority: int = CHECK_PRIORITY,
        timeout: float | None = None,
    ) -> None:
        self._port = port
        self._topology = topology
        # Every selector THIS instance answers to (name and cli_alias): the ADR
        # promises both address it, and matching only the name sent an aliased
        # row down the quiet sibling-backlog branch (HATS-1545 F4).
        self._mine = frozenset({backlog} if isinstance(backlog, str) else backlog)
        self._known = frozenset(known_backlogs) | self._mine
        self._priority = priority
        self._timeout = EDGE_CHECK_TIMEOUT_S if timeout is None else timeout

    def subscriptions(self) -> Sequence[Subscription]:
        return [Subscription(Selector(ANY, ANY), Phase.IN_LOCK, self._priority)]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if not callable(getattr(self._port, "check_declarations", None)):
            return self._without_declarations()
        bound = self._bound_to(Edge(ctx.event.from_state, ctx.event.to_state))
        if not bound:
            return None
        runner = getattr(self._port, "run_check", None)
        if not callable(runner):
            return self._without_executor(bound)
        notes: list[str] = []
        for declaration in bound:
            outcome = runner(
                CheckRequest(
                    declaration=declaration,
                    task_id=ctx.task.id,
                    force=ctx.force,
                    timeout=self._timeout,
                    event=ctx.event.key,
                    lock_expires_at=ctx.lock_expires_at,
                )
            )
            if outcome.ok:
                continue
            if declaration.on_error == "warn" and outcome.downgradable:
                notes.append(
                    f"checks: {declaration.label} broke, downgraded by on_error: warn — "
                    f"{_oneline(outcome.reason)}"
                )
                continue
            raise AbortOperation(outcome.reason)
        return Delta(work_log=tuple(notes)) if notes else None

    def _bound_to(self, edge: Edge) -> tuple[CheckDeclaration, ...]:
        """The declarations this edge fires — THIS edge's rows first, then addressing.

        Addressing is decided HERE, where the mounted names are known, which is
        what makes a typo loud without making a sibling backlog's row fatal: a
        name no backlog of this root answers to is a refusal, a name that
        belongs to a sibling is simply skipped (HATS-1545 R10).

        It is asked only of rows this edge would actually fire (HATS-1576). Asked
        first, it made an unaddressable row fatal on EVERY edge — a project whose
        backlog is named `blog` mounts no `tasks`, every shipped row addresses
        `apps.rack.tasks`, and the whole tracker stopped; `--force` cannot reach
        it, since ``ctx.force`` travels inside the request built after this. It
        is the same argument the point filter already carried: one bad
        ``reviw->done`` must not abort an unrelated ``brainstorm->plan``.
        A point naming an edge this topology lacks stays a skip for that reason.
        """  # comment-length: allow — which miss is loud, and WHERE, is the contract
        declared = self._declarations()
        edges = all_edges(self._topology)
        bound: list[CheckDeclaration] = []
        for row in declared:
            if not self._fires_on(row, edge, edges):
                continue
            if not self._addresses_me(row):
                continue
            if row.kind != CHECK_ROW:
                # A consent row spawns nothing. It is carried this far so the
                # addressing check above sees it — that is the whole reason the
                # kind exists (HATS-1682) — and it stops here, before run_check.
                continue
            bound.append(row)
        return tuple(bound)

    def _fires_on(self, row: CheckDeclaration, edge: Edge, edges: Sequence[Edge]) -> bool:
        """Whether ``row`` denotes THIS event, among events this topology has.

        Two questions in one loop, and the order matters: a selector matching
        nothing here is a SKIP (a sibling backlog's row and a typo are the same
        fact from a subscriber holding one topology), and only a selector that
        does denote events here may then claim this one.
        """
        for point in row.points():
            selector = parse_selector(point)
            if not _hits(selector, edges):
                continue
            if selector.matches(edge):
                return True
        return False

    def _addresses_me(self, row: CheckDeclaration) -> bool:
        """Whether ``row`` is addressed to THIS backlog. Loud on a name nothing has.

        The words are :func:`unaddressed_reason`'s — the same ones the doctor
        prints for the same miss, so the recipe cannot drift between them.
        """
        if len(row.path) != 1 or row.path[0] not in self._known:
            raise AbortOperation(unaddressed_reason(row, sorted(self._known)))
        return row.path[0] in self._mine

    def _declarations(self) -> Sequence[CheckDeclaration]:
        """Ask the port, and turn any trouble into this channel's own refusal —
        a traceback out of an in-lock subscriber is a defect, not a message."""
        try:
            return self._port.check_declarations()
        except AbortOperation:
            raise
        except Exception as exc:
            raise AbortOperation(f"checks: {exc}") from exc

    def _without_declarations(self) -> Delta:
        """A port that cannot even be ASKED, said out loud (HATS-1541 review F1).

        Returning an empty set here instead is the fail-open this channel exists
        to remove: every declared gate vanishes and the transition passes with
        nothing written anywhere. It does not refuse, for the same reason
        :meth:`_without_executor` decides per row — bricking every transition on
        a skewed integrator is the HATS-1538 class. Unlike that case there are no
        rows to consult, because the method that would list them is the missing
        one, so the note is the whole verdict.
        """  # comment-length: allow — why it speaks but does not refuse is the fix
        return Delta(
            work_log=(
                "checks: the integrator supplying this backlog exposes no "
                "ai_hats_rack.checks.CheckPort.check_declarations — no declared gate "
                "can be read, so none ran; transitions stay ungated until it is wired",
            )
        )

    def _without_executor(self, bound: Sequence[CheckDeclaration]) -> Delta | None:
        """Decided per row (D11 clause 4): refusing everything bricks a rack
        that simply has no integrator, and passing everything is the silence
        the channel exists to remove."""
        notes: list[str] = []
        for declaration in bound:
            reason = (
                f"checks: {declaration.label} is bound under apps.rack, but the "
                f"integrator supplying this backlog exposes no check executor "
                f"(ai_hats_rack.checks.CheckPort.run_check) — the gate cannot run"
            )
            if declaration.on_error == "refuse":
                raise AbortOperation(reason)
            notes.append(f"{reason}; downgraded by on_error: warn")
        return Delta(work_log=tuple(notes)) if notes else None


def check_subscriber(
    definition: BacklogDefinition,
    *,
    port: Any,
    known_backlogs: Sequence[str] = (),
) -> CheckSubscriber:
    """The ONE place a backlog's check subscriber is built (HATS-1575).

    Topology and both selectors are derived here, from the definition the kernel
    runs. Before this the integrator derived all three and handed them back, so
    the wiring existed only where the integrator had written it out — the tasks
    kernel — and every other road (sibling backlogs, the workspace the reflect
    consumers mount) had no way to redo a derivation that was not theirs to make.
    """  # comment-length: allow — which side owns the derivation is the fix
    return CheckSubscriber(
        port,
        topology=definition.topology,
        backlog=(definition.name, definition.cli_alias or definition.name),
        known_backlogs=known_backlogs,
    )


def _oneline(reason: str) -> str:
    return " / ".join(line.strip() for line in reason.splitlines() if line.strip())


__all__ = [
    "ARMED",
    "CHECK_PRIORITY",
    "CHECK_ROW",
    "CONSENT_ROW",
    "EDGE_CHECK_TIMEOUT_S",
    "CheckDeclaration",
    "CheckOutcome",
    "CheckPort",
    "CheckPortFactory",
    "CheckRequest",
    "CheckSubscriber",
    "DEAD",
    "FOREIGN",
    "UNADDRESSED",
    "BindingStatus",
    "check_subscriber",
    "classify_bindings",
    "dead_selector_reason",
    "parse_selector",
    "unaddressed_reason",
]
