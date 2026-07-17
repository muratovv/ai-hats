"""FSM topology loaded from ``fsm.yaml`` — the in-package SSOT (HATS-1020).

The engine owns no hardcoded state table: :func:`load_topology` reads the
packaged ``fsm.yaml`` and every guard decision, CLI error message, and event
key derives from it. Changing the file changes the kernel contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from .errors import RackConfigError, RackError

#: PROP-012: the companion-HYP timing rule is anchored to the ``document``
#: state — the accepted obligation becomes unsatisfiable if it disappears.
REQUIRED_STATES: tuple[str, ...] = ("document",)


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
    for required in REQUIRED_STATES:
        if required not in states:
            # PROP-012: accepted obligations reference this state by name.
            raise TopologyError(f"{source}: required state '{required}' is missing")
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
    """Load and validate the topology; default source is the packaged fsm.yaml."""
    if path is not None:
        text, source = path.read_text(encoding="utf-8"), str(path)
    else:
        resource = resources.files("ai_hats_rack").joinpath("fsm.yaml")
        text, source = resource.read_text(encoding="utf-8"), "ai_hats_rack/fsm.yaml"
    return _validate(yaml.safe_load(text), source)
