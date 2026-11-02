"""Layer order and assignment (ADR-0035 D3): every composite file is
``N = expr`` at its layer, then overrides, then the three unnamed slots
``library.default``, machine ``active`` and the CLI. A slot without a
leading sign replaces what is below it; a leading sign appends.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ProcessingError
from .expr import Expr, canonical
from .names import Tree
from .parse import Composite, Override
from .roots import Library


@dataclass(frozen=True)
class Binding:
    #: Index of the layer in the chain, lowest first.
    layer: int
    composite: Composite


@dataclass(frozen=True)
class Program:
    tree: Tree
    #: Per full name, its assignments in layer order.
    bindings: Mapping[str, tuple[Binding, ...]]
    #: After every assignment, in ``(layer, path)`` order. Same name: upper layer wins.
    overrides: tuple[Override, ...]
    #: The unnamed top-level expression.
    top: Expr


def unnamed(*slots: Expr | None) -> Expr:
    """Fold the unnamed slots. A leading sign appends; anything else replaces."""
    current: Expr | None = None
    for slot in slots:
        if slot is None:
            continue
        if slot.leading:
            if current is None:
                raise ProcessingError(
                    f"{canonical(slot)!r} starts with a sign, but there is nothing below it "
                    "to append to"
                )
            current = Expr(terms=current.terms + slot.terms, leading=current.leading)
            continue
        current = slot
    if current is None:
        raise ProcessingError(
            '-re needs an expression, an active value, or library.default: ai-hats -re "<expr>"'
        )
    return current


def program(
    library: Library,
    tree: Tree,
    cli: Expr | None,
    *,
    default: Expr | None = None,
    active: Expr | None = None,
) -> Program:
    top = unnamed(default, active, cli)
    bindings: dict[str, list[Binding]] = {}
    chosen: dict[str, tuple[int, Override]] = {}
    for index, layer in enumerate(library.layers):
        for entity in layer.entities:
            if isinstance(entity, Composite):
                bindings.setdefault(entity.name, []).append(Binding(index, entity))
            elif isinstance(entity, Override):
                chosen[entity.name] = (index, entity)
    overrides = tuple(
        entity
        for _, entity in sorted(
            chosen.values(), key=lambda item: (item[0], item[1].path.as_posix())
        )
    )
    return Program(
        tree=tree,
        bindings={name: tuple(rows) for name, rows in bindings.items()},
        overrides=overrides,
        top=top,
    )
