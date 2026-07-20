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

from .cardschema import Validator, build_card_schema
from .definition import BacklogDefinition, Bindings, HandlerRef
from .dispatch import (
    Phase,
    ReadSubscriber,
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


class HandlerProtocolError(RackConfigError):
    """A factory produced an object that is not a valid subscriber — fail-closed,
    naming the handler/extension and the factory (ADR-0017 §4). Structural
    composition invariant, routed to the RackConfigError subtree."""

    def __init__(self, name: str, factory: Any, obj: Any, needs: str) -> None:
        self.handler = name
        self.factory = getattr(factory, "__qualname__", None) or getattr(
            factory, "__name__", None
        ) or repr(factory)
        super().__init__(
            f"factory {self.factory} for '{name}' produced a {type(obj).__name__}, not {needs}"
        )


def _instantiate(
    ref: HandlerRef,
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> Any:
    factory = factories.get(ref.name)
    if factory is None:
        raise UnknownHandlerError(ref.name, factories.keys())
    return factory(defn, catalog, ref.config)


def _bound_handler(
    ref: HandlerRef,
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> Any:
    """Instantiate a declaration-bound handler and check the surface the loader
    wraps into a :class:`Subscriber` (``name`` + ``on_event``; ``subscriptions``
    are computed by the loader, so the raw handler is NOT itself a Subscriber)."""
    obj = _instantiate(ref, defn, catalog, factories)
    if not (isinstance(getattr(obj, "name", None), str) and callable(getattr(obj, "on_event", None))):
        raise HandlerProtocolError(ref.name, factories.get(ref.name), obj, "a handler (name + on_event)")
    return obj


def _bound_read_handler(
    ref: HandlerRef,
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> Any:
    """Instantiate a declaration-bound READ handler and check its surface
    (``name`` + ``on_read``). A read handler reacts via ``on_read`` — not
    ``on_event`` — so it is validated and wrapped apart from transition handlers
    (HATS-1064)."""
    obj = _instantiate(ref, defn, catalog, factories)
    if not (isinstance(getattr(obj, "name", None), str) and callable(getattr(obj, "on_read", None))):
        raise HandlerProtocolError(
            ref.name, factories.get(ref.name), obj, "a read handler (name + on_read)"
        )
    return obj


def build_extensions(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """Ambient extensions (top-level ``extensions:``): self-subscribing
    subscribers of non-edge / all-edge events, one per reference (ADR-0017 §4).
    A factory returning a non-:class:`Subscriber` fails closed naming it."""
    out: list[Subscriber] = []
    for ref in defn.bindings.extensions:
        obj = _instantiate(ref, defn, catalog, factories)
        if not isinstance(obj, Subscriber):
            raise HandlerProtocolError(ref.name, factories.get(ref.name), obj, "a Subscriber")
        out.append(obj)
    return out


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


class BoundReadSubscriber:
    """A declaration-bound READ handler + loader-computed subscriptions
    (HATS-1064): mirrors :class:`BoundSubscriber` but forwards ``on_read`` (never
    ``on_event``), so it reacts only in the READ phase. Optional ``bind`` /
    ``requires_states`` forward to the wrapped handler."""

    def __init__(self, handler: Any, subscriptions: Sequence[Subscription]) -> None:
        self._handler = handler
        self.name = handler.name
        self._subs = tuple(subscriptions)

    def subscriptions(self) -> Sequence[Subscription]:
        return self._subs

    def on_read(self, ctx: Any) -> Any:
        return self._handler.on_read(ctx)

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
        handler = _bound_handler(group["ref"], defn, catalog, factories)
        phase = getattr(handler, "PHASE", Phase.IN_LOCK)
        subs = [Subscription(key, phase, prio) for key, prio in group["keys"].items()]
        out.append(BoundSubscriber(handler, subs))
    return out


def build_link_subscribers(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """Declaration-bound link handlers (``links.kinds[].handlers``): each ref
    fires IN-LOCK on link AND unlink of its kind — subscription keys
    ``link:<kind>`` / ``unlink:<kind>`` (ADR-0017 §3). Same positional band /
    explicit-pin scheme as the state/edge builder; refs sharing (name, config)
    collapse to one handler with deduped keys (fires once per link event). The
    cross-backlog mirror (``link-target:<kind>``) is HATS-1044, not built here."""
    b: Bindings = defn.bindings
    band = [100]

    def _priority(ref: HandlerRef) -> int:
        if ref.priority is not None:
            return ref.priority
        p = band[0]
        band[0] += 10
        return p

    groups: dict[tuple[str, Any], dict[str, Any]] = {}
    for kind, refs in b.kind_handlers.items():
        for ref in refs:
            prio = _priority(ref)
            gk = (ref.name, _freeze(ref.config))
            group = groups.setdefault(gk, {"ref": ref, "keys": {}})
            for key in (f"link:{kind}", f"unlink:{kind}"):
                group["keys"].setdefault(key, prio)  # dedup: first band/pin wins

    out: list[Subscriber] = []
    for group in groups.values():
        handler = _bound_handler(group["ref"], defn, catalog, factories)
        phase = getattr(handler, "PHASE", Phase.IN_LOCK)
        subs = [Subscription(key, phase, prio) for key, prio in group["keys"].items()]
        out.append(BoundSubscriber(handler, subs))
    return out


def build_read_subscribers(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[ReadSubscriber]:
    """Declaration-bound read handlers (``links.kinds[].read``): each ref fires
    READ-phase on a context read of a card carrying its kind — subscription key
    ``read:<kind>`` (HATS-1064). PHASE is FORCED to ``Phase.READ`` (a read builder
    is read-phase by construction), never read off the handler, so a missing
    attribute cannot silently misfile the subscription. NOT composed into the
    kernel — the read path (``context`` CLI) builds these and hands them to
    ``build_context``. Refs sharing (name, config) collapse to one handler."""
    b: Bindings = defn.bindings
    band = [100]

    def _priority(ref: HandlerRef) -> int:
        if ref.priority is not None:
            return ref.priority
        p = band[0]
        band[0] += 10
        return p

    groups: dict[tuple[str, Any], dict[str, Any]] = {}
    for kind, refs in b.kind_read_handlers.items():
        for ref in refs:
            prio = _priority(ref)
            gk = (ref.name, _freeze(ref.config))
            group = groups.setdefault(gk, {"ref": ref, "keys": {}})
            group["keys"].setdefault(f"read:{kind}", prio)  # dedup: first band/pin wins

    out: list[ReadSubscriber] = []
    for group in groups.values():
        handler = _bound_read_handler(group["ref"], defn, catalog, factories)
        subs = [Subscription(key, Phase.READ, prio) for key, prio in group["keys"].items()]
        out.append(BoundReadSubscriber(handler, subs))
    return out


def compose_subscribers(
    defn: BacklogDefinition,
    catalog: Path,
    factories: Mapping[str, ExtensionFactory],
) -> list[Subscriber]:
    """All definition-derived subscribers: ambient ``extensions:`` +
    declaration-bound state/edge handlers + declaration-bound link handlers,
    resolved through one registry (ADR-0017 §4)."""
    return (
        build_extensions(defn, catalog, factories)
        + build_bound_subscribers(defn, catalog, factories)
        + build_link_subscribers(defn, catalog, factories)
    )


def stock_factories(sections: Sequence[Section] | None = None) -> dict[str, ExtensionFactory]:
    """The rack's stock factories (ADR-0017 §4). ``sections`` threads the plan
    catalog into the gate/scaffold closures; derived-views is NOT stock — it
    needs the integrator's STATE.md path, so it stays a code-channel append."""
    from .extensions import (
        ClearLifecycleHandler,
        FrozenIntegrityExtension,
        ParentContextExtension,
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
        "parent-context": lambda defn, catalog, cfg: ParentContextExtension(
            defn.links_registry, section=cfg.get("section")
        ),
    }


def stock_validators() -> dict[str, Validator]:
    """The rack's stock field validators (ADR-0017 §4). Empty by default — the
    packaged tasks backlog declares none; HYP/PROP land theirs in HATS-1044."""
    return {}


__all__ = [
    "BoundReadSubscriber",
    "BoundSubscriber",
    "ExtensionFactory",
    "HandlerProtocolError",
    "RequiresStatesError",
    "UnknownHandlerError",
    "bind_subscribers",
    "build_bound_subscribers",
    "build_card_schema",
    "build_extensions",
    "build_link_subscribers",
    "build_read_subscribers",
    "compose_subscribers",
    "stock_factories",
    "stock_validators",
    "validate_requires_states",
]
