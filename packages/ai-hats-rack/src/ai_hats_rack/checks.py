"""The FSM half of the ``checks:`` channel: the rack owns its own point names.

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
from typing import Any, Protocol, Sequence, runtime_checkable

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
    """One carried binding. ``handle`` is opaque — the rack never looks inside.

    ``label`` is whatever the carrier wants a refusal to say about the row
    (who declared it, what it binds); the rack quotes it and never parses it.
    """

    point: str
    on_error: str
    label: str
    handle: Any


@dataclass(frozen=True)
class CheckRequest:
    """One firing, handed back to the carrier's executor."""

    declaration: CheckDeclaration
    task_id: str
    force: bool
    timeout: float


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
        priority: int = CHECK_PRIORITY,
        timeout: float | None = None,
    ) -> None:
        self._port = port
        self._topology = topology
        self._priority = priority
        self._timeout = EDGE_CHECK_TIMEOUT_S if timeout is None else timeout

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in all_edge_keys(self._topology)
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
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
        """The declarations this edge fires — grammar first, then topology.

        A point outside this grammar, or naming an edge no state of this
        topology has, is skipped rather than refused: from the carrier's side a
        sibling backlog's row and a typo are the same fact, and only here are
        all the mounted topologies known. Refusing on it is what let one bad
        ``edge:reviw--done`` abort an unrelated ``edge:brainstorm--plan``.
        """
        declared = self._declarations()
        edges = set(all_edge_keys(self._topology))
        return tuple(
            row
            for row in declared
            if parse_edge_point(row.point) is not None
            and row.point in edges
            and row.point == event_key
        )

    def _declarations(self) -> Sequence[CheckDeclaration]:
        """Ask the port, and turn any trouble into this channel's own refusal —
        a traceback out of an in-lock subscriber is a defect, not a message."""
        source = getattr(self._port, "check_declarations", None)
        if not callable(source):
            return ()
        try:
            return source()
        except AbortOperation:
            raise
        except Exception as exc:
            raise AbortOperation(f"checks: {exc}") from exc

    def _without_executor(self, bound: Sequence[CheckDeclaration]) -> Delta | None:
        """Decided per row (D11 clause 4): refusing everything bricks a rack
        that simply has no integrator, and passing everything is the silence
        the channel exists to remove."""
        notes: list[str] = []
        for declaration in bound:
            reason = (
                f"checks: {declaration.label} is bound to {declaration.point}, but the "
                f"integrator supplying this backlog exposes no check executor "
                f"(ai_hats_rack.checks.CheckPort.run_check) — the gate cannot run"
            )
            if declaration.on_error == "refuse":
                raise AbortOperation(reason)
            notes.append(f"{reason}; downgraded by on_error: warn")
        return Delta(work_log=tuple(notes)) if notes else None


def _oneline(reason: str) -> str:
    return " / ".join(line.strip() for line in reason.splitlines() if line.strip())


__all__ = [
    "CHECK_PRIORITY",
    "EDGE_CHECK_TIMEOUT_S",
    "EDGE_PREFIX",
    "CheckDeclaration",
    "CheckOutcome",
    "CheckPort",
    "CheckRequest",
    "CheckSubscriber",
    "parse_edge_point",
]
