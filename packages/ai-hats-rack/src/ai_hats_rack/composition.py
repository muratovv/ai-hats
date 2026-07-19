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
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .definition import BacklogDefinition, Bindings, HandlerRef
from .dispatch import (
    Phase,
    RequiresStatesError,
    Subscriber,
    Subscription,
    bind_subscribers,
    validate_requires_states,
)
from .errors import RackConfigError
from .fsm import Topology

if TYPE_CHECKING:
    from .extensions.sections import Section

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


class BoundSubscriber:
    """A declaration-bound handler + the subscriptions the LOADER computed for it
    (ADR-0017 §3): the handler hardcodes no keys — its ``on_event`` runs on the
    event keys the definition's slots expand to. Optional protocol hooks
    (``bind``/``requires_states``) forward to the wrapped handler."""

    def __init__(self, handler: Any, subscriptions: Sequence[Subscription]) -> None:
        self._handler = handler
        self.name = handler.name
        self._subs = tuple(subscriptions)

    def subscriptions(self) -> Sequence[Subscription]:
        return self._subs

    def on_event(self, ctx: Any) -> Any:
        return self._handler.on_event(ctx)

    def __getattr__(self, item: str) -> Any:
        handler = self.__dict__.get("_handler")
        if handler is None:
            raise AttributeError(item)
        return getattr(handler, item)


def _self_loops(topology: Topology) -> set[str]:
    """States carrying a DECLARED self-edge (reclaim precedent, ADR-0017 §3): the
    on_enter/on_exit product includes ``edge:S--S`` only for these."""
    return {s for s in topology.states if s in topology.edges.get(s, ())}


def _product_keys(kind: str, target: Any, topology: Topology, self_loops: set[str]) -> list[tuple[str, str]]:
    if kind == "edge":
        return [target]
    state = target
    if kind == "on_enter":
        pairs = [(src, state) for src in topology.states if src != state]
    else:  # on_exit
        pairs = [(state, dst) for dst in topology.states if dst != state]
    if state in self_loops:
        pairs.append((state, state))
    return pairs


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def build_bound_subscribers(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """Declaration-bound handlers (``on_enter``/``on_exit``/``edges[].handlers``):
    the loader expands each reference to its event keys — the FULL edge product
    for state slots (forced non-topology entries included, HATS-518), the exact
    edge for edge handlers — honoring ``edges[].skip``. A reference without an
    explicit ``priority:`` gets a positional band (100, 110, …) in on_enter →
    on_exit → edge-handler order; explicit pins keep their number (ADR-0017 §3).
    References sharing (name, config) collapse to one handler with deduped keys,
    so a declared handler fires exactly once per event."""
    topology = defn.topology
    b: Bindings = defn.bindings
    self_loops = _self_loops(topology)

    band = [100]

    def _priority(ref: HandlerRef) -> int:
        if ref.priority is not None:
            return ref.priority
        p = band[0]
        band[0] += 10
        return p

    # Records in canonical band order: on_enter (state order), on_exit, edges.
    records: list[tuple[HandlerRef, str, Any, int]] = []
    for state in topology.states:
        for ref in b.state_on_enter.get(state, ()):
            records.append((ref, "on_enter", state, _priority(ref)))
    for state in topology.states:
        for ref in b.state_on_exit.get(state, ()):
            records.append((ref, "on_exit", state, _priority(ref)))
    for edge, refs in b.edge_handlers.items():
        for ref in refs:
            records.append((ref, "edge", edge, _priority(ref)))

    groups: dict[tuple[str, Any], dict[str, Any]] = {}
    for ref, kind, target, prio in records:
        keys = {
            f"edge:{src}--{dst}": prio
            for (src, dst) in _product_keys(kind, target, topology, self_loops)
            if ref.name not in b.edge_skips.get((src, dst), frozenset())
        }
        gk = (ref.name, _freeze(ref.config))
        group = groups.setdefault(gk, {"ref": ref, "keys": {}})
        for key, key_prio in keys.items():
            group["keys"].setdefault(key, key_prio)  # dedup: first band/pin wins

    out: list[Subscriber] = []
    for group in groups.values():
        handler = _instantiate(group["ref"], defn, catalog, factories)
        phase = getattr(handler, "PHASE", Phase.IN_LOCK)
        subs = [Subscription(key, phase, prio) for key, prio in group["keys"].items()]
        out.append(BoundSubscriber(handler, subs))
    return out


def compose_subscribers(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """All definition-derived subscribers: ambient ``extensions:`` +
    declaration-bound handlers, resolved through one registry (ADR-0017 §4)."""
    return build_extensions(defn, catalog, factories) + build_bound_subscribers(
        defn, catalog, factories
    )


def stock_factories(sections: Sequence[Section] | None = None) -> dict[str, ExtensionFactory]:
    """The rack's stock factories (ADR-0017 §4). ``sections`` threads the plan
    catalog into the gate/scaffold closures; derived-views is NOT stock — it
    needs the integrator's STATE.md path, so it stays a code-channel append."""
    from .extensions import (
        ClearLifecycleHandler,
        FrozenIntegrityExtension,
        PlanGateExtension,
        PlanScaffoldExtension,
        StampLifecycleHandler,
    )
    from .extensions.sections import DEFAULT_PLAN_SECTIONS

    catalog_sections = tuple(sections) if sections is not None else DEFAULT_PLAN_SECTIONS

    def _field(cfg: Mapping[str, Any]) -> str:
        return str(cfg.get("field", "completed_at"))

    return {
        "frozen-integrity": lambda defn, catalog, cfg: FrozenIntegrityExtension(
            catalog, topology=defn.topology
        ),
        "plan-gate": lambda defn, catalog, cfg: PlanGateExtension(catalog, catalog_sections),
        "plan-scaffold": lambda defn, catalog, cfg: PlanScaffoldExtension(catalog, catalog_sections),
        "stamp-lifecycle": lambda defn, catalog, cfg: StampLifecycleHandler(_field(cfg)),
        "clear-lifecycle": lambda defn, catalog, cfg: ClearLifecycleHandler(_field(cfg)),
    }


__all__ = [
    "BoundSubscriber",
    "ExtensionFactory",
    "RequiresStatesError",
    "UnknownHandlerError",
    "bind_subscribers",
    "build_bound_subscribers",
    "build_extensions",
    "compose_subscribers",
    "stock_factories",
    "validate_requires_states",
]
