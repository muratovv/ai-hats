"""Unified backlog definition loaded from ``backlog.yaml`` (HATS-1042, ADR-0017 §1).

One immutable :class:`BacklogDefinition` folds the FSM topology (states + edges,
formerly ``fsm.yaml``) and the link-kind registry (formerly ``links.yaml``) into
the single artifact every module is constructed from. The packaged default is a
LOSSLESS fold of both (§6).

The loader is fail-closed: a section or key not yet materialized in HATS-1042 is
a typed error naming the key and its successor task, never a silent no-op — a
declared-but-inert gate is exactly the failure mode this prevents
(hats-1014-fsm §4). Structural validation reuses ``fsm._validate`` /
``registry._validate`` so REQUIRED_STATES and the inverse-pair invariants stay
in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .errors import RackConfigError
from .fsm import Topology, _validate as _validate_topology
from .registry import LinksRegistry, _validate as _validate_registry

#: A key declared but not materialized in HATS-1042 → the task that owns it.
_KEY_SUCCESSORS: Mapping[str, str] = {
    "fields": "HATS-1035",
    "extras": "HATS-1035",
    "on_enter": "HATS-1043",
    "on_exit": "HATS-1043",
    "handlers": "HATS-1043",
    "skip": "HATS-1043",
    "extensions": "HATS-1043",
    "targets": "HATS-1044",
}

_TOP_KEYS = frozenset({"name", "prefix", "fsm", "links"})
_FSM_KEYS = frozenset({"initial", "states", "edges"})
_STATE_KEYS = frozenset({"name"})
_EDGE_KEYS = frozenset({"from", "to", "name"})
_LINKS_KEYS = frozenset({"kinds"})
_KIND_KEYS = frozenset({"name", "arity", "inverse", "derived", "aliases"})


class BacklogDefinitionError(RackConfigError):
    """backlog.yaml is malformed or violates a structural invariant."""


class UnsupportedBacklogKeyError(BacklogDefinitionError):
    """A key not materialized in HATS-1042; names the key and its successor task."""

    def __init__(self, key: str, location: str) -> None:
        self.key = key
        self.successor = _KEY_SUCCESSORS.get(key)
        if self.successor is not None:
            detail = f"materialized by {self.successor}, not this loader"
        else:
            detail = "not a supported backlog key (HATS-1042)"
        super().__init__(f"backlog.yaml {location}: key {key!r} — {detail}")


@dataclass(frozen=True)
class BacklogDefinition:
    """Immutable definition of one backlog: identity + folded topology/registry.

    ``edge_names`` maps a declared edge ``(from, to)`` to its optional name and
    lives SEPARATE from :class:`Topology` (whose adjacency shape is consumed
    positionally in four edge-key derivation sites) so a name never perturbs
    edge-key derivation.
    """

    name: str
    prefix: str
    topology: Topology
    links_registry: LinksRegistry
    edge_names: Mapping[tuple[str, str], str]


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], location: str) -> None:
    for key in mapping:
        if key not in allowed:
            raise UnsupportedBacklogKeyError(str(key), location)


def _state_name(raw: Any, source: str) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        _reject_unknown(raw, _STATE_KEYS, f"state {raw['name']!r}")
        return raw["name"]
    raise BacklogDefinitionError(f"{source}: each fsm.state needs a string 'name' (got {raw!r})")


def _collect_fsm(
    raw: Any, source: str
) -> tuple[Any, list[str], dict[str, list[str]], dict[tuple[str, str], str]]:
    """Key-check + shape-collect the ``fsm`` block; NO structural validation yet.

    Returns ``(initial, states, adjacency, edge_names)``. Unsupported keys are
    rejected here — before ``_validate_topology`` — so a fail-closed key surfaces
    even when the surrounding topology is otherwise incomplete.
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
    states = [_state_name(s, source) for s in states_raw]
    adjacency: dict[str, list[str]] = {name: [] for name in states}
    edge_names: dict[tuple[str, str], str] = {}
    for item in edges_raw:
        if not isinstance(item, dict):
            raise BacklogDefinitionError(f"{source}: each fsm.edge must be a mapping (got {item!r})")
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
    return raw.get("initial"), states, adjacency, edge_names


def _collect_links(raw: Any, source: str) -> list[Any]:
    """Key-check the ``links`` block; return raw kinds for ``_validate_registry``."""
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: 'links' must be a mapping")
    _reject_unknown(raw, _LINKS_KEYS, "links")
    kinds_raw = raw.get("kinds")
    if not isinstance(kinds_raw, list):
        raise BacklogDefinitionError(f"{source}: links.kinds must be a list")
    for item in kinds_raw:
        if isinstance(item, dict):
            name = item.get("name")
            loc = f"kind {name!r}" if isinstance(name, str) else "kind"
            _reject_unknown(item, _KIND_KEYS, loc)
    return kinds_raw


def _build(raw: Any, source: str) -> BacklogDefinition:
    if not isinstance(raw, dict):
        raise BacklogDefinitionError(f"{source}: expected a mapping at top level")
    # Reject every unsupported key BEFORE structural validation — the fold must
    # never let a successor-task section slip through inert.
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
    initial, states, adjacency, edge_names = _collect_fsm(raw["fsm"], source)
    kinds_raw = _collect_links(raw["links"], source)
    topology = _validate_topology(
        {"initial": initial, "states": states, "edges": adjacency}, source
    )
    registry = _validate_registry({"kinds": kinds_raw}, source)
    return BacklogDefinition(
        name=name,
        prefix=prefix,
        topology=topology,
        links_registry=registry,
        edge_names=MappingProxyType(edge_names),
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
