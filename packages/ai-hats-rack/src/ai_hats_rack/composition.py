"""Composition root: resolve a backlog definition's declared handlers into
subscribers via one open factory registry (HATS-1043, ADR-0017 §4).

Every referenced name resolves against the registry — stock factories ship
here, the integrator registers its own (worktree/ownership scope) as closures
before building; an unknown name is a typed, fail-closed error naming it. The
bind / requires_states composition helpers live in ``dispatch`` (no import
cycle) and are re-exported here so the composition root has one home.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .definition import BacklogDefinition, HandlerRef
from .dispatch import (
    RequiresStatesError,
    Subscriber,
    bind_subscribers,
    validate_requires_states,
)
from .errors import RackConfigError

#: ADR-0017 §4 factory signature, definition-first — project scope (repo root,
#: STATE.md path) enters as a closure at registration, never in the signature.
ExtensionFactory = Callable[[BacklogDefinition, Path, Mapping[str, Any]], Subscriber]


class UnknownHandlerError(RackConfigError):
    """A declaration references a handler/extension name with no registered
    factory — fail-closed, naming the name (ADR-0017 §4 materialization rule).
    Structural composition invariant, routed to the RackConfigError subtree."""

    def __init__(self, name: str, known: Sequence[str]) -> None:
        self.handler = name
        self.known = tuple(sorted(known))
        super().__init__(
            f"handler '{name}' is referenced in the backlog definition but no "
            f"factory is registered for it; registered: {list(self.known)}"
        )


def _instantiate(
    ref: HandlerRef,
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> Subscriber:
    factory = factories.get(ref.name)
    if factory is None:
        raise UnknownHandlerError(ref.name, factories.keys())
    return factory(defn, catalog, ref.config)


def build_extensions(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """Ambient extensions (top-level ``extensions:``): self-subscribing
    subscribers of non-edge / all-edge events, one per reference (ADR-0017 §4)."""
    return [_instantiate(ref, defn, catalog, factories) for ref in defn.bindings.extensions]


__all__ = [
    "ExtensionFactory",
    "RequiresStatesError",
    "UnknownHandlerError",
    "bind_subscribers",
    "build_extensions",
    "validate_requires_states",
]
