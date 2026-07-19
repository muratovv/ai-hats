"""FSM topology — the ``fsm`` section of a backlog definition (HATS-1020).

The engine owns no hardcoded state table: :func:`load_topology` reads it from
the packaged ``backlog.yaml`` (fsm.yaml folded in, HATS-1042) and every guard
decision, CLI error message, and event key derives from it. Changing the file
changes the kernel contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .errors import RackConfigError, RackError

# The load-time `document` anchor (PROP-012) moved to extension-declared
# `requires_states()`, checked at composition (ADR-0017 §3/§6) — a tasks-
# discipline fact, so an HYP/PROP topology with no `document` now loads.


class TopologyError(RackConfigError):
    """fsm.yaml is malformed or violates a structural invariant."""


class UnknownStateError(RackError):
    """A state name not present in the loaded topology."""

    def __init__(self, state: str, known: tuple[str, ...]) -> None:
        self.state = state
        self.known = known
        super().__init__(f"Unknown state '{state}'. Known states: {', '.join(known)}")


class InvalidTransitionError(RackError):
    """The FSM guard refused an edge; carries the legal targets (PROP-061)."""

    def __init__(
        self, task_id: str, from_state: str, to_state: str, allowed: tuple[str, ...]
    ) -> None:
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed
        legal = ", ".join(allowed) if allowed else "none (terminal state)"
        super().__init__(
            f"Invalid transition for {task_id}: {from_state} → {to_state}. "
            f"Legal edges from '{from_state}': {legal}"
        )


@dataclass(frozen=True)
class Topology:
    """Immutable FSM topology: states + directed edges + initial state."""

    initial: str
    states: tuple[str, ...]
    edges: Mapping[str, tuple[str, ...]]

    def require_state(self, state: str) -> None:
        if state not in self.states:
            raise UnknownStateError(state, self.states)

    def targets(self, from_state: str) -> tuple[str, ...]:
        self.require_state(from_state)
        return self.edges[from_state]

    def allows(self, from_state: str, to_state: str) -> bool:
        self.require_state(to_state)
        return to_state in self.targets(from_state)

    def guard(self, task_id: str, from_state: str, to_state: str) -> None:
        """Raise :class:`InvalidTransitionError` unless the edge is legal."""
        if not self.allows(from_state, to_state):
            raise InvalidTransitionError(task_id, from_state, to_state, self.targets(from_state))


def _validate(raw: object, source: str) -> Topology:
    if not isinstance(raw, dict):
        raise TopologyError(f"{source}: expected a mapping at top level")
    states_raw = raw.get("states")
    edges_raw = raw.get("edges")
    initial = raw.get("initial")
    if not isinstance(states_raw, list) or not all(isinstance(s, str) for s in states_raw):
        raise TopologyError(f"{source}: 'states' must be a list of strings")
    states = tuple(states_raw)
    if len(set(states)) != len(states):
        raise TopologyError(f"{source}: duplicate state names")
    if not isinstance(initial, str) or initial not in states:
        raise TopologyError(f"{source}: 'initial' must name a declared state")
    if not isinstance(edges_raw, dict):
        raise TopologyError(f"{source}: 'edges' must be a mapping")
    if set(edges_raw) != set(states):
        missing = set(states) - set(edges_raw)
        extra = set(edges_raw) - set(states)
        raise TopologyError(
            f"{source}: edges must cover every state exactly "
            f"(missing: {sorted(missing)}, undeclared: {sorted(extra)})"
        )
    edges: dict[str, tuple[str, ...]] = {}
    for src, targets in edges_raw.items():
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            raise TopologyError(f"{source}: edges[{src!r}] must be a list of state names")
        unknown = [t for t in targets if t not in states]
        if unknown:
            raise TopologyError(f"{source}: edges[{src!r}] point at undeclared states {unknown}")
        edges[src] = tuple(targets)
    return Topology(initial=initial, states=states, edges=MappingProxyType(edges))


def load_topology(path: Path | None = None) -> Topology:
    """The FSM topology of a backlog: packaged ``backlog.yaml`` by default, or
    the backlog at ``path``. A thin accessor over :func:`load_backlog` (deferred
    import — it validates via this module's ``_validate``) so the many no-arg
    callers stay unchanged with ``backlog.yaml`` as the one source (HATS-1042)."""
    from .definition import load_backlog

    return load_backlog(path).topology
