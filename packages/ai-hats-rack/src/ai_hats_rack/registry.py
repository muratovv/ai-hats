"""Link-kind registry loaded from ``links.yaml`` — the in-package SSOT for the
open set of edge kinds a backlog understands (HATS-1028, epic HATS-1014).

The kernel and models stay kind-blind: this registry is *injected config*
(mirroring :mod:`ai_hats_rack.fsm`'s ``Topology``/``fsm.yaml``). It declares
structure only — kind names, the legacy-field mapping, inverse pairs, which
kind is derived — while *semantics* are bound from above by extensions
(epic-automation reads the hierarchy kind). Legacy fields keep their storage,
so the registry is a read/write projection with zero data migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import yaml

if TYPE_CHECKING:
    from .models import TaskCard


class LinksRegistryError(Exception):
    """links.yaml is malformed or violates a structural invariant."""


class UnknownLinkKindError(Exception):
    """A kind name not present in the loaded registry; names the configured set."""

    def __init__(self, kind: str, configured: Sequence[str]) -> None:
        self.kind = kind
        self.configured = tuple(configured)
        super().__init__(
            f"Unknown link kind {kind!r}: configured kinds are {', '.join(self.configured)}"
        )


class DerivedLinkKindError(Exception):
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
    """One declared edge kind. ``legacy_field`` empty → stored under the generic
    ``links:`` key; ``inverse == name`` marks a symmetric kind (metadata)."""

    name: str
    legacy_field: str = ""
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
        renamed ``parent`` kind is still found."""
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
        legacy_field=str(raw.get("legacy_field") or ""),
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
    """Load and validate the registry; default source is the packaged links.yaml.

    Mirrors :func:`ai_hats_rack.fsm.load_topology`: an explicit ``path`` is a
    per-backlog override, otherwise the in-package default is the SSOT.
    """
    if path is not None:
        text, source = path.read_text(encoding="utf-8"), str(path)
    else:
        resource = resources.files("ai_hats_rack").joinpath("links.yaml")
        text, source = resource.read_text(encoding="utf-8"), "ai_hats_rack/links.yaml"
    return _validate(yaml.safe_load(text), source)


def load_registry_for(project_dir: Path) -> LinksRegistry:
    """Per-backlog registry: a project-root ``links.yaml`` override, else the
    packaged default. This is what makes the registry per-backlog and open."""
    override = project_dir / "links.yaml"
    return load_registry(override) if override.is_file() else load_registry()


def _read_kind(kind: LinkKind, card: TaskCard) -> list[str]:
    """The stored ids for one non-derived kind (legacy field or ``links:`` key)."""
    if kind.legacy_field:
        raw = getattr(card, kind.legacy_field, None)
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

    Legacy fields are read as the kinds their mapping names; new kinds come from
    the generic ``links:`` dict; derived kinds (children) are filled from
    ``derived`` (the caller computes them, e.g. via ``kernel.children_of``).
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
