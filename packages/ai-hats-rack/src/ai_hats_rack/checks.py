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
from .fsm import Topology, all_edge_keys
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

#: The namespace this package owns. A name outside it belongs to some other
#: application and is none of this subscriber's business.
EDGE_PREFIX = "edge:"


@dataclass(frozen=True)
class CheckDeclaration:
    """One carried row. ``handle`` is opaque — the rack never looks inside.

    Since HATS-1545 the carrier does not know this package's grammar: it hands
    over ``path`` (where the row sat under ``apps.rack`` — the backlog it names)
    and ``cargo`` (every key ai-hats does not own), and THIS side decides what
    they mean. ``label`` is whatever the carrier wants a refusal to say about
    the row; the rack quotes it and never parses it.
    """

    path: tuple[str, ...]
    at: tuple[str, ...]
    cargo: Mapping[str, Any]
    on_error: str
    label: str
    handle: Any

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


def parse_edge_point(point: str) -> tuple[str, str] | None:
    """``edge:<from>--<to>`` → the pair, or ``None`` for any other name.

    ``None`` is not an error: the checks DSL is shared by several applications
    and a name this grammar does not accept is simply addressed elsewhere.
    """
    if not point.startswith(EDGE_PREFIX):
        return None
    src, sep, dst = point[len(EDGE_PREFIX) :].partition("--")
    if not sep or not src or not dst:
        return None
    return src, dst


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
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in all_edge_keys(self._topology)
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if not callable(getattr(self._port, "check_declarations", None)):
            return self._without_declarations()
        bound = self._bound_to(ctx.event.key)
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

    def _bound_to(self, event_key: str) -> tuple[CheckDeclaration, ...]:
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
        ``edge:reviw--done`` must not abort an unrelated ``edge:brainstorm--plan``.
        A point naming an edge this topology lacks stays a skip for that reason.
        """  # comment-length: allow — which miss is loud, and WHERE, is the contract
        declared = self._declarations()
        edges = set(all_edge_keys(self._topology))
        bound: list[CheckDeclaration] = []
        for row in declared:
            if not self._fires_on(row, event_key, edges):
                continue
            if not self._addresses_me(row):
                continue
            bound.append(row)
        return tuple(bound)

    def _fires_on(self, row: CheckDeclaration, event_key: str, edges: set[str]) -> bool:
        """Whether ``row`` names THIS event among the points this topology has."""
        for point in row.points():
            if parse_edge_point(point) is None or point not in edges:
                continue
            if point == event_key:
                return True
        return False

    def _addresses_me(self, row: CheckDeclaration) -> bool:
        """Whether ``row`` is addressed to THIS backlog. Loud on a name nothing has."""
        if len(row.path) != 1:
            raise AbortOperation(
                f"checks: {row.label} sits at apps.rack{''.join('.' + p for p in row.path)}, but a "
                f"rack row is declared one level down, under the backlog it gates "
                f"(apps.rack.<backlog>) — this one names {'no backlog' if not row.path else 'a deeper path'}"
            )
        name = row.path[0]
        if name not in self._known:
            mounted = sorted(self._known)
            raise AbortOperation(
                f"checks: {row.label} is declared under apps.rack.{name}, but no backlog of this "
                f"project answers to {name!r} (mounted: {', '.join(mounted)}) — "
                f"a gate on a backlog that does not exist would never fire. "
                f"Give the backlog it means `cli_alias: {name}` in its backlog.yaml, so it "
                f"answers to both selectors — that is the fix when the row ships with a role "
                f"you do not own. Otherwise re-address the row to one of: "
                f"{', '.join('apps.rack.' + s for s in mounted)}."
            )
        return name in self._mine

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
    "CHECK_PRIORITY",
    "EDGE_CHECK_TIMEOUT_S",
    "EDGE_PREFIX",
    "CheckDeclaration",
    "CheckOutcome",
    "CheckPort",
    "CheckPortFactory",
    "CheckRequest",
    "CheckSubscriber",
    "check_subscriber",
    "parse_edge_point",
]
