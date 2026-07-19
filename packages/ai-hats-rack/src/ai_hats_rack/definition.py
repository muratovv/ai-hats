"""Unified backlog definition loaded from ``backlog.yaml`` (HATS-1042, ADR-0017 §1).

One immutable :class:`BacklogDefinition` folds the FSM topology (states + edges,
formerly ``fsm.yaml``) and the link-kind registry (formerly ``links.yaml``) into
the single artifact every module is constructed from. The packaged default is a
LOSSLESS fold of both (§6).

The loader is fail-closed: a key this loader does not materialize is a typed
error naming the key, never a silent no-op — a declared-but-inert gate is
exactly the failure mode this prevents (hats-1014-fsm §4). Structural
validation reuses ``fsm._validate`` / ``registry._validate`` so REQUIRED_STATES
and the inverse-pair invariants stay in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .errors import RackConfigError
from .fsm import Topology, _validate as _validate_topology
from .registry import LinksRegistry, LinksRegistryError, _validate as _validate_registry

# Allow-sets load FROM the packaged `backlog-schema.yaml` grammar so no hardcoded
# frozenset can drift; reserved keys (fields/extras/targets) stay out of `keys`.
def _load_schema() -> Mapping[str, Any]:
    text = resources.files("ai_hats_rack").joinpath("backlog-schema.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


_SCHEMA_KEYS = _load_schema()["keys"]
_TOP_KEYS = frozenset(_SCHEMA_KEYS["top"])
_FSM_KEYS = frozenset(_SCHEMA_KEYS["fsm"])
_STATE_KEYS = frozenset(_SCHEMA_KEYS["state"])
_EDGE_KEYS = frozenset(_SCHEMA_KEYS["edge"])
_LINKS_KEYS = frozenset(_SCHEMA_KEYS["links"])
_KIND_KEYS = frozenset(_SCHEMA_KEYS["kind"])


class BacklogDefinitionError(RackConfigError):
    """backlog.yaml is malformed or violates a structural invariant."""


class UnsupportedBacklogKeyError(BacklogDefinitionError):
    """A key this loader does not materialize; fail-closed, never a silent no-op."""

    def __init__(self, key: str, location: str) -> None:
        self.key = key
        super().__init__(
            f"backlog.yaml {location}: key {key!r} is not supported by this "
            f"loader (the backlog.yaml surface lands in phases — see ADR-0017)"
        )


class LegacyLinksOverrideError(LinksRegistryError):
    """A project-root ``links.yaml`` override is retired (ADR-0017 §1, HATS-1042
    R6): its kinds must be folded into the catalog's ``backlog.yaml``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            f"{path} is retired — the link-kind registry now lives in backlog.yaml "
            f"(ADR-0017 §1). Fold its 'kinds:' into a 'links:' section of the "
            f"catalog's backlog.yaml and delete {path.name}."
        )


@dataclass(frozen=True)
class HandlerRef:
    """One declaration-bound / ambient handler reference (ADR-0017 §4): a bare
    name or a ``{name, ...}`` mapping. ``priority`` is the explicit pin (``None``
    → the composition root assigns a positional band); ``config`` is the
    remaining keys handed verbatim to the factory."""

    name: str
    config: Mapping[str, Any] = field(default_factory=dict)
    priority: int | None = None


@dataclass(frozen=True)
class Bindings:
    """The handler surface of a definition (ADR-0017 §3-§4): declaration-bound
    slots (state ``on_enter``/``on_exit``, ``edges[].handlers``/``skip``,
    ``kinds[].handlers``) plus ambient top-level ``extensions``. Parsed here;
    turned into subscriptions at the composition root (``composition.py``)."""

    state_on_enter: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    state_on_exit: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    edge_handlers: Mapping[tuple[str, str], tuple[HandlerRef, ...]] = field(default_factory=dict)
    edge_skips: Mapping[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    kind_handlers: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    extensions: tuple[HandlerRef, ...] = ()


@dataclass(frozen=True)
class BacklogDefinition:
    """Immutable definition of one backlog: identity + folded topology/registry.

    ``edge_names`` maps a declared edge ``(from, to)`` to its optional name and
    lives SEPARATE from :class:`Topology` (whose adjacency shape is consumed
    positionally in four edge-key derivation sites) so a name never perturbs
    edge-key derivation. ``bindings`` carries the declared handler surface.
    """

    name: str
    prefix: str
    topology: Topology
    links_registry: LinksRegistry
    edge_names: Mapping[tuple[str, str], str]
    bindings: Bindings = field(default_factory=Bindings)


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], location: str) -> None:
    for key in mapping:
        if key not in allowed:
            raise UnsupportedBacklogKeyError(str(key), location)


def _parse_ref(raw: Any, location: str) -> HandlerRef:
    """A handler reference is a bare name OR a ``{name, priority?, ...config}``
    mapping; ``priority`` pins the numeric order, the rest is factory config."""
    if isinstance(raw, str):
        return HandlerRef(name=raw)
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        priority = raw.get("priority")
        if priority is not None and not isinstance(priority, int):
            raise BacklogDefinitionError(
                f"{location}: handler '{raw['name']}' priority must be an int (got {priority!r})"
            )
        config = {k: v for k, v in raw.items() if k not in ("name", "priority")}
        return HandlerRef(name=raw["name"], config=MappingProxyType(config), priority=priority)
    raise BacklogDefinitionError(
        f"{location}: a handler reference is a name or a mapping with 'name' (got {raw!r})"
    )


def _parse_refs(raw: Any, location: str) -> tuple[HandlerRef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BacklogDefinitionError(f"{location}: expected a list of handler references (got {raw!r})")
    return tuple(_parse_ref(item, location) for item in raw)


def _parse_skip(raw: Any, location: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise BacklogDefinitionError(f"{location}: 'skip' must be a list of handler names (got {raw!r})")
    return frozenset(raw)


def _collect_state(
    raw: Any, source: str
) -> tuple[str, tuple[HandlerRef, ...], tuple[HandlerRef, ...]]:
    """Return ``(name, on_enter refs, on_exit refs)`` for one fsm.state entry."""
    if isinstance(raw, str):
        return raw, (), ()
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        name = raw["name"]
        _reject_unknown(raw, _STATE_KEYS, f"state {name!r}")
        return (
            name,
            _parse_refs(raw.get("on_enter"), f"state {name!r} on_enter"),
            _parse_refs(raw.get("on_exit"), f"state {name!r} on_exit"),
        )
    raise BacklogDefinitionError(f"{source}: each fsm.state needs a string 'name' (got {raw!r})")


@dataclass(frozen=True)
class _FsmShape:
    initial: Any
    states: list[str]
    adjacency: dict[str, list[str]]
    edge_names: dict[tuple[str, str], str]
    state_on_enter: dict[str, tuple[HandlerRef, ...]]
    state_on_exit: dict[str, tuple[HandlerRef, ...]]
    edge_handlers: dict[tuple[str, str], tuple[HandlerRef, ...]]
    edge_skips: dict[tuple[str, str], frozenset[str]]


def _collect_fsm(raw: Any, source: str) -> _FsmShape:
    """Key-check + shape-collect the ``fsm`` block (topology + binding slots); NO
    structural validation yet. Unsupported keys are rejected here — before
    ``_validate_topology`` — so a fail-closed key surfaces even when the
    surrounding topology is otherwise incomplete.
    """
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: 'fsm' must be a mapping")
    _reject_unknown(raw, _FSM_KEYS, "fsm")
    states_raw = raw.get("states")
    edges_raw = raw.get("edges")
    if not isinstance(states_raw, list):
        raise BacklogDefinitionError(f"{source}: fsm.states must be a list")
    if not isinstance(edges_raw, list):
        raise BacklogDefinitionError(f"{source}: fsm.edges must be a list")
    states: list[str] = []
    state_on_enter: dict[str, tuple[HandlerRef, ...]] = {}
    state_on_exit: dict[str, tuple[HandlerRef, ...]] = {}
    for s in states_raw:
        name, on_enter, on_exit = _collect_state(s, source)
        states.append(name)
        if on_enter:
            state_on_enter[name] = on_enter
        if on_exit:
            state_on_exit[name] = on_exit
    adjacency: dict[str, list[str]] = {name: [] for name in states}
    edge_names: dict[tuple[str, str], str] = {}
    edge_handlers: dict[tuple[str, str], tuple[HandlerRef, ...]] = {}
    edge_skips: dict[tuple[str, str], frozenset[str]] = {}
    for item in edges_raw:
        if not isinstance(item, dict):
            raise BacklogDefinitionError(
                f"{source}: each fsm.edge must be a mapping (got {item!r})"
            )
        frm, to = item.get("from"), item.get("to")
        _reject_unknown(item, _EDGE_KEYS, f"edge {frm}--{to}")
        if not isinstance(frm, str) or not isinstance(to, str):
            raise BacklogDefinitionError(f"{source}: edge {frm}--{to} needs string 'from' and 'to'")
        adjacency.setdefault(frm, []).append(to)
        name = item.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise BacklogDefinitionError(f"{source}: edge {frm}--{to} name must be a string")
            edge_names[(frm, to)] = name
        handlers = _parse_refs(item.get("handlers"), f"edge {frm}--{to} handlers")
        if handlers:
            edge_handlers[(frm, to)] = handlers
        skip = _parse_skip(item.get("skip"), f"edge {frm}--{to} skip")
        if skip:
            edge_skips[(frm, to)] = skip
    return _FsmShape(
        raw.get("initial"), states, adjacency, edge_names,
        state_on_enter, state_on_exit, edge_handlers, edge_skips,
    )


def _collect_links(raw: Any, source: str) -> tuple[list[Any], dict[str, tuple[HandlerRef, ...]]]:
    """Key-check the ``links`` block; return ``(raw kinds, kind→handlers)``. The
    ``kinds[].handlers`` slot is PARSED here (HATS-1043); link/unlink dispatch is
    HATS-1043 step 6, so no link subscriptions are built from it yet."""
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: 'links' must be a mapping")
    _reject_unknown(raw, _LINKS_KEYS, "links")
    kinds_raw = raw.get("kinds")
    if not isinstance(kinds_raw, list):
        raise BacklogDefinitionError(f"{source}: links.kinds must be a list")
    kind_handlers: dict[str, tuple[HandlerRef, ...]] = {}
    for item in kinds_raw:
        if isinstance(item, dict):
            name = item.get("name")
            loc = f"kind {name!r}" if isinstance(name, str) else "kind"
            _reject_unknown(item, _KIND_KEYS, loc)
            handlers = _parse_refs(item.get("handlers"), f"{loc} handlers")
            if handlers and isinstance(name, str):
                kind_handlers[name] = handlers
    return kinds_raw, kind_handlers


def _build(raw: Any, source: str) -> BacklogDefinition:
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: expected a mapping at top level")
    # Reject unsupported keys BEFORE structural validation — an unsupported
    # section must fail as itself, not as an unrelated topology error.
    _reject_unknown(raw, _TOP_KEYS, "top level")
    name = raw.get("name")
    prefix = raw.get("prefix")
    if not isinstance(name, str) or not name:
        raise BacklogDefinitionError(f"{source}: 'name' must be a non-empty string")
    if not isinstance(prefix, str) or not prefix:
        raise BacklogDefinitionError(f"{source}: 'prefix' must be a non-empty string")
    if "fsm" not in raw:
        raise BacklogDefinitionError(f"{source}: missing 'fsm' section")
    if "links" not in raw:
        raise BacklogDefinitionError(f"{source}: missing 'links' section")
    fsm = _collect_fsm(raw["fsm"], source)
    kinds_raw, kind_handlers = _collect_links(raw["links"], source)
    extensions = _parse_refs(raw.get("extensions"), "extensions")
    topology = _validate_topology(
        {"initial": fsm.initial, "states": fsm.states, "edges": fsm.adjacency}, source
    )
    registry = _validate_registry({"kinds": kinds_raw}, source)
    bindings = Bindings(
        state_on_enter=MappingProxyType(fsm.state_on_enter),
        state_on_exit=MappingProxyType(fsm.state_on_exit),
        edge_handlers=MappingProxyType(fsm.edge_handlers),
        edge_skips=MappingProxyType(fsm.edge_skips),
        kind_handlers=MappingProxyType(kind_handlers),
        extensions=extensions,
    )
    return BacklogDefinition(
        name=name,
        prefix=prefix,
        topology=topology,
        links_registry=registry,
        edge_names=MappingProxyType(fsm.edge_names),
        bindings=bindings,
    )


def load_backlog(path: Path | None = None) -> BacklogDefinition:
    """Load a backlog definition; default source is the packaged ``backlog.yaml``.

    An explicit ``path`` is a per-backlog file; ``None`` is today's zero-config
    default (the packaged tasks contract).
    """
    if path is not None:
        text, source = path.read_text(encoding="utf-8"), str(path)
    else:
        resource = resources.files("ai_hats_rack").joinpath("backlog.yaml")
        text, source = resource.read_text(encoding="utf-8"), "ai_hats_rack/backlog.yaml"
    return _build(yaml.safe_load(text), source)


def resolve_definition(
    catalog: Path,
    *,
    prefix_alias: str | None = None,
    project_dir: Path | None = None,
) -> BacklogDefinition:
    """Instance resolution (ADR-0017 §1, single-backlog): the definition a catalog runs on.

    A catalog holding ``backlog.yaml`` uses that file whole — its ``prefix:`` is
    authoritative. A catalog WITHOUT one is the tasks backlog on the packaged
    default (today's zero-config), and only THEN does ``prefix_alias`` (the
    deprecated ai-hats.yaml ``task_prefix``) override the packaged prefix.

    ``project_dir`` (when given) makes a legacy project-root ``links.yaml``
    override fail closed (R6) — reads and transitions resolve identically, so a
    retired override never silently applies on one path and errors on the other.
    """
    if project_dir is not None and (project_dir / "links.yaml").is_file():
        raise LegacyLinksOverrideError(project_dir / "links.yaml")
    catalog_file = catalog / "backlog.yaml"
    if catalog_file.is_file():
        return load_backlog(catalog_file)
    defn = load_backlog()
    if prefix_alias and prefix_alias != defn.prefix:
        return replace(defn, prefix=prefix_alias)
    return defn
