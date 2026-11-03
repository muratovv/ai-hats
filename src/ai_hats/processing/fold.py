"""Expansion and fold (ADR-0035 D4): expand to base terms with signs — a
composite opens in place, its sign multiplying its terms' — then fold left to
right: ``+X`` appends X if absent, ``-X`` removes it wherever it stands.

The topmost binding of a composite then opens each matching override, so
those terms are brought by the override (ADR-0035 D3). What leaves and who
brought what is the trace (D8); a member a composite's subtraction carries
away while someone else also brings it is a warning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..diagnostics import Diagnostic, Level
from ..provenance import ComponentLayer
from ..surfaces import TraceEntry
from .errors import ProcessingError
from .expr import Expr
from .names import resolve_term
from .order import Binding, Program
from .parse import Composite, Entity, Override


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


_RANK = {ComponentLayer.BUILT_IN: 0, ComponentLayer.GLOBAL: 1, ComponentLayer.PROJECT: 2}


def _refuse_foreign(referrer: ComponentLayer, entity: Entity, term: str) -> None:
    """A file may name its own owner and owners below it, never one above."""
    target = entity.owner
    if _RANK[target] > _RANK[referrer]:
        raise ProcessingError(
            f"{term!r} is owned by {target.value}, and a {referrer.value} file may not name it"
        )


def _matching(program: Program, composite: Composite) -> tuple[Override, ...]:
    """Overrides whose ``apply`` fullmatches the composite's name or a tag."""
    labels = (composite.name, *composite.tags)
    return tuple(
        override
        for override in program.overrides
        if any(override.apply.fullmatch(label) for label in labels)
    )


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
        referrer: ComponentLayer | None,
    ) -> None:
        for i, term in enumerate(expr.terms):
            entity = resolve_term(program.tree, term.name)
            if referrer is not None:
                _refuse_foreign(referrer, entity, term.name)
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
            opened = (*stack, key)
            visit(
                binding.composite.expr,
                sign=here_sign,
                declared_in=entity.name,
                carrier=here_carrier if here_sign < 0 else entity.name,
                defining=binding,
                top_index=index,
                stack=opened,
                referrer=binding.composite.owner,
            )
            # The inner binding a self-reference looks down to is not re-armed.
            if binding is program.bindings[entity.name][-1]:
                for override in _matching(program, binding.composite):
                    visit(
                        override.expr,
                        sign=here_sign,
                        declared_in=override.name,
                        carrier=here_carrier if here_sign < 0 else override.name,
                        defining=binding,
                        top_index=index,
                        stack=opened,
                        referrer=override.owner,
                    )

    visit(
        program.top,
        sign=+1,
        declared_in=identity,
        carrier=identity,
        defining=None,
        top_index=None,
        stack=(),
        referrer=None,
    )
    return tuple(out)


def fold(
    signed: tuple[Signed, ...],
    diagnostics: list[Diagnostic],
    *,
    consent: frozenset[str] = frozenset(),
) -> Folded:
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
        who = brought_by.pop(term.name)
        if term.name in consent:
            diagnostics.append(
                Diagnostic(
                    Level.WARN,
                    f"{term.carrier} disarms consent {term.name}, which {who} armed",
                )
            )
        removed.append(TraceEntry(term.name, who, removed_by=term.carrier))
        first_top.pop(term.name, None)
        contributors.pop(term.name, None)

    present = [TraceEntry(name, by, removed_by=None) for name, by in composites.items()]
    present += [TraceEntry(name, brought_by[name], removed_by=None) for name in members]
    return Folded(
        members=tuple(members),
        trace=(*present, *removed),
        first_top_index=dict(first_top),
    )


def _warn_unselected(program: Program, diagnostics: list[Diagnostic]) -> None:
    """An override that fullmatches no composite in the merged tree is a warning, not silence."""
    composites = [
        entity for entity in program.tree.entities.values() if isinstance(entity, Composite)
    ]
    for override in program.overrides:
        if any(
            override.apply.fullmatch(label)
            for composite in composites
            for label in (composite.name, *composite.tags)
        ):
            continue
        diagnostics.append(
            Diagnostic(Level.WARN, f"{override.name} selects no composite", where=override.path)
        )


def expand_and_fold(program: Program, identity: str, diagnostics: list[Diagnostic]) -> Folded:
    from .parse import Hook

    _warn_unselected(program, diagnostics)
    consent = frozenset(
        name
        for name, entity in program.tree.entities.items()
        if isinstance(entity, Hook) and entity.kind == "consent"
    )
    return fold(expand(program, identity), diagnostics, consent=consent)
