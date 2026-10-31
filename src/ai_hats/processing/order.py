"""Layer order and assignment (ADR-0035 D3): every composite file is
``N = expr`` at its layer, the CLI expression is the unnamed last word.

Overrides, ``library.default`` and machine ``active`` join this order with
their own stage; until then a leading sign on the CLI has nothing below it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ProcessingError
from .expr import Expr, canonical
from .names import Tree
from .parse import Composite
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
    #: The unnamed top-level expression.
    top: Expr


def program(library: Library, tree: Tree, cli: Expr) -> Program:
    if cli.leading:
        raise ProcessingError(
            f"{canonical(cli)!r} starts with a sign, but there is nothing below it to append to"
        )
    bindings: dict[str, list[Binding]] = {}
    for index, layer in enumerate(library.layers):
        for entity in layer.entities:
            if isinstance(entity, Composite):
                bindings.setdefault(entity.name, []).append(Binding(index, entity))
    return Program(
        tree=tree,
        bindings={name: tuple(rows) for name, rows in bindings.items()},
        top=cli,
    )
