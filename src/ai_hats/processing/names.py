"""Name resolution (ADR-0035 D2): the path is the name, a term is a suffix on
segment boundaries over the merged tree, last-wins by full name between layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..diagnostics import Diagnostic, Level
from .errors import ProcessingError
from .parse import TERM_TYPES, Entity, entity_type
from .roots import Library


@dataclass(frozen=True)
class Tree:
    """The merged tree: every full name to the entity of its topmost layer."""

    entities: Mapping[str, Entity]


def merge(library: Library, diagnostics: list[Diagnostic]) -> Tree:
    entities: dict[str, Entity] = {}
    for layer in library.layers:
        for entity in layer.entities:
            below = entities.get(entity.name)
            if below is not None and entity_type(below) != entity_type(entity):
                diagnostics.append(
                    Diagnostic(
                        Level.WARN,
                        f"{entity.name}: a {entity_type(entity)} overrides a "
                        f"{entity_type(below)} of the same name from {below.path}",
                        where=entity.path,
                    )
                )
            entities[entity.name] = entity
    return Tree(entities=entities)


def resolve(tree: Tree, term: str) -> str:
    """The one full name ``term`` is a suffix of. An exact name wins; zero or several suffixes refuse."""
    if term in tree.entities:
        return term
    candidates = [name for name in tree.entities if name.endswith("::" + term)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ProcessingError(f"unknown name {term!r}")
    listed = ", ".join(sorted(candidates))
    raise ProcessingError(f"{term!r} is ambiguous — it names {listed}; write a longer suffix")


def resolve_term(tree: Tree, term: str) -> Entity:
    """The entity a term names, refusing a type that is never a term."""
    entity = tree.entities[resolve(tree, term)]
    kind = entity_type(entity)
    if kind not in TERM_TYPES:
        raise ProcessingError(f"{term!r} names {entity.name}, a(n) {kind}, which is not a term")
    return entity
