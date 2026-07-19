"""Link-kind registry — the ``links`` section of a backlog definition, the open
set of edge kinds a backlog understands (HATS-1028, epic HATS-1014).

The kernel and models stay kind-blind: this registry is *injected config*
(mirroring :mod:`ai_hats_rack.fsm`'s ``Topology``), declaring structure only —
names, inverse pairs, derived flag — while *semantics* bind from above
(epic-automation reads the hierarchy kind). A kind's NAME is its storage field
(HATS-1032): a dedicated task.yaml link field, else the generic ``links:`` map —
a read/write projection with zero data migration. Loaded from the packaged
``backlog.yaml`` (links.yaml folded in, HATS-1042).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .errors import RackConfigError, RackError
from .models import LINK_STORAGE_FIELDS

if TYPE_CHECKING:
    from .models import TaskCard


class LinksRegistryError(RackConfigError):
    """The links section is malformed or violates a structural invariant."""


class UnknownLinkKindError(RackError):
    """A kind name not present in the loaded registry; names the configured set."""

    def __init__(self, kind: str, configured: Sequence[str]) -> None:
        self.kind = kind
        self.configured = tuple(configured)
        super().__init__(
            f"Unknown link kind {kind!r}: configured kinds are {', '.join(self.configured)}"
        )


class DerivedLinkKindError(RackError):
    """A derived kind (e.g. ``children``) cannot be linked/unlinked directly."""

    def __init__(self, kind: str, inverse: str) -> None:
        self.kind = kind
        self.inverse = inverse
        super().__init__(
            f"Link kind {kind!r} is derived (the computed reverse of {inverse!r}); "
            f"it is never stored — link the {inverse!r} side instead"
        )


@dataclass(frozen=True)
class LinkKind:
    """One declared edge kind. The ``name`` IS the storage field (HATS-1032): a
    dedicated task.yaml link field when the name is one, else the generic
    ``links:`` key; ``inverse == name`` marks a symmetric kind (metadata)."""

    name: str
    arity: str = "many"  # "one" (scalar id) | "many" (list of ids)
    inverse: str = ""
    derived: bool = False
    aliases: tuple[str, ...] = ()

    @property
    def stored(self) -> bool:
        return not self.derived

    @property
    def symmetric(self) -> bool:
        return bool(self.inverse) and self.inverse == self.name


@dataclass(frozen=True)
class LinksRegistry:
    """Immutable, ordered set of link kinds + a name/alias lookup."""

    kinds: tuple[LinkKind, ...]
    by_name: Mapping[str, LinkKind] = field(repr=False)

    def names(self) -> tuple[str, ...]:
        return tuple(k.name for k in self.kinds)

    def get(self, name: str) -> LinkKind | None:
        return self.by_name.get(name)

    def require(self, name: str) -> LinkKind:
        """Resolve a kind by name or alias; unknown → typed error (names the set)."""
        kind = self.by_name.get(name)
        if kind is None:
            raise UnknownLinkKindError(name, self.names())
        return kind

    def stored_kinds(self) -> tuple[LinkKind, ...]:
        return tuple(k for k in self.kinds if k.stored)

    @property
    def hierarchy_kind(self) -> LinkKind | None:
        """The stored kind whose inverse is a *derived* kind — the parent edge
        `is_epic` and epic-automation bind to. Structural, not name-based, so a
        renamed hierarchy kind is still found."""
        for kind in self.kinds:
            if kind.derived:
                continue
            inverse = self.get(kind.inverse) if kind.inverse else None
            if inverse is not None and inverse.derived:
                return kind
        return None

    @property
    def children_kind(self) -> LinkKind | None:
        """The derived inverse of the hierarchy kind (default ``children``)."""
        hierarchy = self.hierarchy_kind
        if hierarchy is None:
            return None
        return self.get(hierarchy.inverse)

    def parent_of(self, card: TaskCard) -> str:
        """The single hierarchy-parent id of a card, via the configured kind."""
        hierarchy = self.hierarchy_kind
        if hierarchy is None:
            return ""
        ids = _read_kind(hierarchy, card)
        return ids[0] if ids else ""


def _build_registry(kinds: Sequence[LinkKind]) -> LinksRegistry:
    by_name: dict[str, LinkKind] = {}
    for kind in kinds:
        for key in (kind.name, *kind.aliases):
            if key in by_name:
                raise LinksRegistryError(f"duplicate link-kind name/alias {key!r}")
            by_name[key] = kind
    return LinksRegistry(kinds=tuple(kinds), by_name=MappingProxyType(by_name))


def _parse_kind(raw: Any, source: str) -> LinkKind:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise LinksRegistryError(f"{source}: each kind needs a string 'name' (got {raw!r})")
    arity = raw.get("arity", "many")
    if arity not in ("one", "many"):
        raise LinksRegistryError(f"{source}: kind {raw['name']!r} arity must be one|many")
    aliases = raw.get("aliases", []) or []
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise LinksRegistryError(f"{source}: kind {raw['name']!r} aliases must be a string list")
    return LinkKind(
        name=raw["name"],
        arity=arity,
        inverse=str(raw.get("inverse") or ""),
        derived=bool(raw.get("derived", False)),
        aliases=tuple(aliases),
    )


def _validate(raw: object, source: str) -> LinksRegistry:
    if not isinstance(raw, dict):
        raise LinksRegistryError(f"{source}: expected a mapping at top level")
    kinds_raw = raw.get("kinds")
    if not isinstance(kinds_raw, list) or not kinds_raw:
        raise LinksRegistryError(f"{source}: 'kinds' must be a non-empty list")
    kinds = [_parse_kind(item, source) for item in kinds_raw]
    names = {k.name for k in kinds}
    for kind in kinds:
        # A named inverse must exist — dangling inverses would silently disable
        # the hierarchy/symmetry detection they encode.
        if kind.inverse and kind.inverse not in names:
            raise LinksRegistryError(
                f"{source}: kind {kind.name!r} inverse {kind.inverse!r} is not a declared kind"
            )
    return _build_registry(kinds)


def load_registry(path: Path | None = None) -> LinksRegistry:
    """The link-kind registry of a backlog: packaged ``backlog.yaml`` by default,
    or the backlog at ``path``. A thin accessor over :func:`load_backlog` (which
    validates via this module's ``_validate``) — backlog.yaml is the one source
    (links.yaml folded in, HATS-1042)."""
    from .definition import load_backlog

    return load_backlog(path).links_registry


def _read_kind(kind: LinkKind, card: TaskCard) -> list[str]:
    """The stored ids for one non-derived kind (dedicated field or ``links:`` key)."""
    if kind.name in LINK_STORAGE_FIELDS:
        raw = getattr(card, kind.name, None)
        if kind.arity == "one":
            return [raw] if isinstance(raw, str) and raw else []
        return [i for i in (raw or []) if i]
    return [i for i in card.links.get(kind.name, []) if i]


def resolve_links(
    registry: LinksRegistry,
    card: TaskCard,
    *,
    derived: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """Project a card onto ``{kind: [ids]}`` in registry order.

    A kind named after a dedicated task.yaml field reads that field; other kinds
    come from the generic ``links:`` dict; derived kinds (children) are filled
    from ``derived`` (the caller computes them, e.g. via ``kernel.children_of``).
    Empty kinds are omitted so the projection stays byte-clean.
    """
    derived = derived or {}
    out: dict[str, list[str]] = {}
    for kind in registry.kinds:
        ids = list(derived.get(kind.name, [])) if kind.derived else _read_kind(kind, card)
        ids = [i for i in ids if i]
        if ids:
            out[kind.name] = ids
    return out
