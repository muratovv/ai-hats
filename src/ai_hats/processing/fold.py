"""Expansion and fold (ADR-0035 D4): expand to base terms with signs — a
composite opens in place, its sign multiplying its terms' — then fold left to
right: ``+X`` appends X if absent, ``-X`` removes it wherever it stands.

What leaves and who brought what is the trace (D8); a member a composite's
subtraction carries away while someone else also brings it is a warning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..diagnostics import Diagnostic, Level
from ..surfaces.plan import TraceEntry
from .errors import ProcessingError
from .expr import Expr
from .names import resolve_term
from .order import Binding, Program
from .parse import Composite


@dataclass(frozen=True)
class Signed:
    sign: int
    #: Full name.
    name: str
    is_composite: bool
    #: The composite whose expression names the term, or the identity at the top.
    declared_in: str
    #: The statement that holds the sign as folded: the composite or expression
    #: where the outermost negation was written, else ``declared_in``.
    carrier: str
    #: Which top-level term the occurrence descends from.
    top_index: int


@dataclass(frozen=True)
class Folded:
    #: Surviving base terms, in order.
    members: tuple[str, ...]
    trace: tuple[TraceEntry, ...]
    #: For each member, the top-level term its placing occurrence descends from.
    first_top_index: Mapping[str, int]


def expand(program: Program, identity: str) -> tuple[Signed, ...]:
    out: list[Signed] = []

    def binding_for(name: str, defining: Binding | None) -> Binding:
        rows = program.bindings[name]
        if defining is not None and defining.composite.name == name:
            below = [b for b in rows if b.layer < defining.layer]
            if not below:
                raise ProcessingError(
                    f"{defining.composite.path}: expr names {name!r} itself, but no layer "
                    "below defines it"
                )
            return below[-1]
        return rows[-1]

    def visit(
        expr: Expr,
        *,
        sign: int,
        declared_in: str,
        carrier: str,
        defining: Binding | None,
        top_index: int | None,
        stack: tuple[tuple[str, int], ...],
    ) -> None:
        for i, term in enumerate(expr.terms):
            entity = resolve_term(program.tree, term.name)
            here_sign = sign * term.sign
            here_carrier = carrier if sign < 0 else declared_in
            index = i if top_index is None else top_index
            if not isinstance(entity, Composite):
                out.append(Signed(here_sign, entity.name, False, declared_in, here_carrier, index))
                continue
            binding = binding_for(entity.name, defining)
            key = (entity.name, binding.layer)
            if key in stack:
                chain = " -> ".join(name for name, _ in (*stack, key))
                raise ProcessingError(f"cycle through {entity.name!r}: {chain}")
            out.append(Signed(here_sign, entity.name, True, declared_in, here_carrier, index))
            visit(
                binding.composite.expr,
                sign=here_sign,
                declared_in=entity.name,
                carrier=here_carrier if here_sign < 0 else entity.name,
                defining=binding,
                top_index=index,
                stack=(*stack, key),
            )

    visit(
        program.top,
        sign=+1,
        declared_in=identity,
        carrier=identity,
        defining=None,
        top_index=None,
        stack=(),
    )
    return tuple(out)


def fold(signed: tuple[Signed, ...], diagnostics: list[Diagnostic]) -> Folded:
    members: list[str] = []
    brought_by: dict[str, str] = {}
    first_top: dict[str, int] = {}
    contributors: dict[str, list[str]] = {}
    removed: list[TraceEntry] = []
    composites: dict[str, str] = {}

    for term in signed:
        if term.is_composite:
            if term.sign > 0:
                composites.setdefault(term.name, term.declared_in)
            continue
        if term.sign > 0:
            rows = contributors.setdefault(term.name, [])
            if term.declared_in not in rows:
                rows.append(term.declared_in)
            if term.name not in brought_by:
                brought_by[term.name] = term.declared_in
                first_top[term.name] = term.top_index
                members.append(term.name)
            continue
        if term.name not in brought_by:
            continue  # -X over an absent term is the axiom, not a finding
        via_composite = term.declared_in != term.carrier
        others = [c for c in contributors.get(term.name, ()) if c != term.declared_in]
        if via_composite and others:
            diagnostics.append(
                Diagnostic(
                    Level.WARN,
                    f"'- {term.declared_in}' (in {term.carrier}) removes {term.name}, "
                    f"which {', '.join(others)} also brings",
                )
            )
        members.remove(term.name)
        removed.append(TraceEntry(term.name, brought_by.pop(term.name), removed_by=term.carrier))
        first_top.pop(term.name, None)
        contributors.pop(term.name, None)

    present = [TraceEntry(name, by, removed_by=None) for name, by in composites.items()]
    present += [TraceEntry(name, brought_by[name], removed_by=None) for name in members]
    return Folded(
        members=tuple(members),
        trace=(*present, *removed),
        first_top_index=dict(first_top),
    )


def expand_and_fold(program: Program, identity: str, diagnostics: list[Diagnostic]) -> Folded:
    return fold(expand(program, identity), diagnostics)
