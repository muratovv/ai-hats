"""Unified backlog definition loaded from ``backlog.yaml`` (HATS-1042, ADR-0017 §1).

One immutable :class:`BacklogDefinition` folds the FSM topology (states + edges,
formerly ``fsm.yaml``) and the link-kind registry (formerly ``links.yaml``) into
the single artifact every module is constructed from. The packaged default is a
LOSSLESS fold of both (§6).

The loader is fail-closed: a key it does not materialize is a typed error naming
the key, never a silent no-op (hats-1014-fsm §4). Structural validation reuses
``fsm._validate`` / ``registry._validate``; the ``document`` anchor moved from
there to composition-time ``requires_states`` (ADR-0017 §3).
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
    text = (
        resources.files("ai_hats_rack").joinpath("backlog-schema.yaml").read_text(encoding="utf-8")
    )
    return yaml.safe_load(text)


_SCHEMA_KEYS = _load_schema()["keys"]
_TOP_KEYS = frozenset(_SCHEMA_KEYS["top"])
_FSM_KEYS = frozenset(_SCHEMA_KEYS["fsm"])
_STATE_KEYS = frozenset(_SCHEMA_KEYS["state"])
_EDGE_KEYS = frozenset(_SCHEMA_KEYS["edge"])
_LINKS_KEYS = frozenset(_SCHEMA_KEYS["links"])
_KIND_KEYS = frozenset(_SCHEMA_KEYS["kind"])
_FIELD_KEYS = frozenset(_SCHEMA_KEYS["field"])

#: The `type` vocabulary (ADR-0017 §1 field grammar limits): scalars + `any`.
_FIELD_TYPES = frozenset({"str", "int", "list", "any"})
#: The stock reaction a declared stored inverse pair REQUIRES (ADR-0017 §2/R4).
MIRROR_HANDLER = "mirror-link"
#: `emit` policy — declared per field, enforced by the write layer (HATS-1035 step 4).
_EMIT_MODES = frozenset({"always", "when-set"})
#: Top-level unknown-key policy (ADR-0017 §1): today's passthrough is `allow`.
_EXTRAS_POLICIES = frozenset({"allow", "forbid"})


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


class MissingMirrorReactionError(BacklogDefinitionError):
    """A declared STORED inverse pair (``inverse: X`` where X is a stored, non-
    symmetric kind) without the ``mirror-link`` reaction — the reverse edge would
    drift undetected, so the loader refuses it fail-closed (ADR-0017 §2/R4).
    Derived inverses (``children``) and symmetric kinds (``related``) are exempt."""

    def __init__(self, kind: str, inverse: str, source: str) -> None:
        self.kind = kind
        self.inverse = inverse
        super().__init__(
            f"{source}: kind {kind!r} declares a stored inverse {inverse!r} but no "
            f"{MIRROR_HANDLER!r} reaction — add 'handlers: [{MIRROR_HANDLER}]' to "
            f"{kind!r} so the reverse edge stays convergent (ADR-0017 §2)"
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
    ``kinds[].handlers``/``kinds[].read``) plus ambient top-level ``extensions``.
    Parsed here; turned into subscriptions at the composition root
    (``composition.py``). ``kind_read_handlers`` fire READ-phase (HATS-1064)."""

    state_on_enter: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    state_on_exit: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    edge_handlers: Mapping[tuple[str, str], tuple[HandlerRef, ...]] = field(default_factory=dict)
    edge_skips: Mapping[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    kind_handlers: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    kind_read_handlers: Mapping[str, tuple[HandlerRef, ...]] = field(default_factory=dict)
    extensions: tuple[HandlerRef, ...] = ()


@dataclass(frozen=True)
class FieldSpec:
    """One declared card-field (ADR-0017 §1): the schema of everything beyond
    the kernel anchor. ``has_default`` records whether ``default`` was declared
    (vs a required/no-default field); ``validator`` is a bare name resolved
    against the open registry at composition, never a check hidden in code."""

    name: str
    type: str = "str"
    has_default: bool = False
    default: Any = None
    required: bool = False
    choices: tuple[Any, ...] | None = None
    validator: str | None = None
    emit: str = "always"


@dataclass(frozen=True)
class BacklogDefinition:
    """Immutable definition of one backlog: identity + folded topology/registry.

    ``edge_names`` maps a declared edge ``(from, to)`` to its optional name and
    lives SEPARATE from :class:`Topology` (whose adjacency shape is consumed
    positionally in four edge-key derivation sites) so a name never perturbs
    edge-key derivation. ``bindings`` carries the declared handler surface;
    ``fields`` the card schema; ``extras_policy`` the unknown-key write policy.
    """

    name: str
    prefix: str
    topology: Topology
    links_registry: LinksRegistry
    edge_names: Mapping[tuple[str, str], str]
    bindings: Bindings = field(default_factory=Bindings)
    fields: tuple[FieldSpec, ...] = ()
    extras_policy: str = "allow"


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
        raise BacklogDefinitionError(
            f"{location}: expected a list of handler references (got {raw!r})"
        )
    return tuple(_parse_ref(item, location) for item in raw)


def _parse_skip(raw: Any, location: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise BacklogDefinitionError(
            f"{location}: 'skip' must be a list of handler names (got {raw!r})"
        )
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
        raw.get("initial"),
        states,
        adjacency,
        edge_names,
        state_on_enter,
        state_on_exit,
        edge_handlers,
        edge_skips,
    )


def _collect_links(
    raw: Any, source: str
) -> tuple[list[Any], dict[str, tuple[HandlerRef, ...]], dict[str, tuple[HandlerRef, ...]]]:
    """Key-check the ``links`` block; return ``(raw kinds, kind→handlers,
    kind→read-handlers)``. ``kinds[].handlers`` fire link/unlink IN-LOCK
    (HATS-1043); ``kinds[].read`` fire READ-phase on a context read (HATS-1064)."""
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: 'links' must be a mapping")
    _reject_unknown(raw, _LINKS_KEYS, "links")
    kinds_raw = raw.get("kinds")
    if not isinstance(kinds_raw, list):
        raise BacklogDefinitionError(f"{source}: links.kinds must be a list")
    kind_handlers: dict[str, tuple[HandlerRef, ...]] = {}
    kind_read_handlers: dict[str, tuple[HandlerRef, ...]] = {}
    for item in kinds_raw:
        if isinstance(item, dict):
            name = item.get("name")
            loc = f"kind {name!r}" if isinstance(name, str) else "kind"
            _reject_unknown(item, _KIND_KEYS, loc)
            handlers = _parse_refs(item.get("handlers"), f"{loc} handlers")
            if handlers and isinstance(name, str):
                kind_handlers[name] = handlers
            read_handlers = _parse_refs(item.get("read"), f"{loc} read")
            if read_handlers and isinstance(name, str):
                kind_read_handlers[name] = read_handlers
    return kinds_raw, kind_handlers, kind_read_handlers


def _collect_field(raw: Any, source: str) -> FieldSpec:
    """Parse one ``fields[]`` entry, fail-closed on unknown keys and bad enums."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        raise BacklogDefinitionError(
            f"{source}: each field needs a non-empty string 'name' (got {raw!r})"
        )
    name = raw["name"]
    _reject_unknown(raw, _FIELD_KEYS, f"field {name!r}")
    ftype = raw.get("type", "str")
    if ftype not in _FIELD_TYPES:
        raise BacklogDefinitionError(
            f"{source}: field {name!r} type {ftype!r} must be one of {sorted(_FIELD_TYPES)}"
        )
    emit = raw.get("emit", "always")
    if emit not in _EMIT_MODES:
        raise BacklogDefinitionError(
            f"{source}: field {name!r} emit {emit!r} must be one of {sorted(_EMIT_MODES)}"
        )
    choices_raw = raw.get("choices")
    if choices_raw is not None and not isinstance(choices_raw, list):
        raise BacklogDefinitionError(
            f"{source}: field {name!r} choices must be a list (got {choices_raw!r})"
        )
    validator = raw.get("validator")
    if validator is not None and not isinstance(validator, str):
        raise BacklogDefinitionError(
            f"{source}: field {name!r} validator must be a name (got {validator!r})"
        )
    return FieldSpec(
        name=name,
        type=ftype,
        has_default="default" in raw,
        default=raw.get("default"),
        required=bool(raw.get("required", False)),
        choices=tuple(choices_raw) if choices_raw is not None else None,
        validator=validator,
        emit=emit,
    )


def _collect_fields(raw: Any, source: str) -> tuple[FieldSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BacklogDefinitionError(f"{source}: 'fields' must be a list")
    specs = tuple(_collect_field(item, source) for item in raw)
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise BacklogDefinitionError(f"{source}: field {spec.name!r} declared more than once")
        seen.add(spec.name)
    return specs


def _validate_stored_inverses(
    registry: LinksRegistry,
    kind_handlers: Mapping[str, tuple[HandlerRef, ...]],
    source: str,
) -> None:
    """Fail-closed (ADR-0017 §2/R4): a stored kind whose inverse is ANOTHER stored
    kind must declare the ``mirror-link`` reaction, else the reverse edge drifts
    undetected. Derived inverses (``children``) and symmetric kinds (``related``,
    stored one-sided today) are exempt — the packaged tasks default keeps loading."""
    for kind in registry.kinds:
        if kind.derived or not kind.inverse or kind.symmetric:
            continue
        inverse = registry.get(kind.inverse)
        if inverse is None or inverse.derived:
            continue
        refs = kind_handlers.get(kind.name, ())
        if not any(ref.name == MIRROR_HANDLER for ref in refs):
            raise MissingMirrorReactionError(kind.name, kind.inverse, source)


def _parse_extras(raw: Any, source: str) -> str:
    if raw is None:
        return "allow"
    if raw not in _EXTRAS_POLICIES:
        raise BacklogDefinitionError(
            f"{source}: 'extras' {raw!r} must be one of {sorted(_EXTRAS_POLICIES)}"
        )
    return raw


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
    kinds_raw, kind_handlers, kind_read_handlers = _collect_links(raw["links"], source)
    extensions = _parse_refs(raw.get("extensions"), "extensions")
    fields = _collect_fields(raw.get("fields"), source)
    extras_policy = _parse_extras(raw.get("extras"), source)
    topology = _validate_topology(
        {"initial": fsm.initial, "states": fsm.states, "edges": fsm.adjacency}, source
    )
    registry = _validate_registry({"kinds": kinds_raw}, source)
    _validate_stored_inverses(registry, kind_handlers, source)
    bindings = Bindings(
        state_on_enter=MappingProxyType(fsm.state_on_enter),
        state_on_exit=MappingProxyType(fsm.state_on_exit),
        edge_handlers=MappingProxyType(fsm.edge_handlers),
        edge_skips=MappingProxyType(fsm.edge_skips),
        kind_handlers=MappingProxyType(kind_handlers),
        kind_read_handlers=MappingProxyType(kind_read_handlers),
        extensions=extensions,
    )
    return BacklogDefinition(
        name=name,
        prefix=prefix,
        topology=topology,
        links_registry=registry,
        edge_names=MappingProxyType(fsm.edge_names),
        bindings=bindings,
        fields=fields,
        extras_policy=extras_policy,
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


def packaged_definitions() -> tuple[str, ...]:
    """Names of non-default definitions shipped in-package: every
    ``definitions/<name>/backlog.yaml`` resource. The package dir IS the
    registry — no enumeration to keep in sync. NOT auto-mounted:
    Workspace.discover mounts a catalog's OWN backlog.yaml, which wins;
    these seed one (ADR-0017 §5)."""
    root = resources.files("ai_hats_rack").joinpath("definitions")
    return tuple(
        sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir() and entry.joinpath("backlog.yaml").is_file()
        )
    )


def packaged_definition_source(name: str) -> str:
    """Raw YAML text of a shipped non-default definition — what the migration/
    init step writes into a catalog's ``backlog.yaml`` (ADR-0017 §5). Unknown
    name -> a typed, fail-closed error."""
    shipped = packaged_definitions()
    if name not in shipped:
        raise BacklogDefinitionError(
            f"no packaged backlog definition {name!r}; shipped: {list(shipped)}"
        )
    return (
        resources.files("ai_hats_rack")
        .joinpath("definitions")
        .joinpath(name)
        .joinpath("backlog.yaml")
        .read_text(encoding="utf-8")
    )


def load_packaged_definition(name: str) -> BacklogDefinition:
    """Load a shipped non-default definition by name — the packaged HYP/PROP
    contract (ADR-0017 §5). A catalog's own ``backlog.yaml`` still wins at mount
    time (:func:`resolve_definition`); this is the source that seeds one."""
    return _build(
        yaml.safe_load(packaged_definition_source(name)),
        f"ai_hats_rack/definitions/{name}/backlog.yaml",
    )


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
