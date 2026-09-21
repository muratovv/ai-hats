"""Processing — the rules by which the flat DSL becomes a plan (ADR-0035).

Pure functions over values, one module per stage; the one impure input is
``roots.read_roots``. The output is the plan's composition half (ADR-0036 D1).
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_hats_core.layout import ProjectLayout

from ..diagnostics import Diagnostic
from ..surfaces import CompositionPlan
from .errors import ProcessingError
from .expr import canonical, parse_expr
from .roots import Library, Root, candidate_roots, read_roots

__all__ = ["ProcessingError", "Root", "candidate_roots", "compose", "plan_from_library"]


def plan_from_library(
    library: Library,
    expression: str,
    *,
    layout: ProjectLayout,
    diagnostics: list[Diagnostic],
) -> CompositionPlan:
    """The stages after the read, in order: names, order, fold, prompt, emit."""
    from .emit import emit
    from .fold import expand_and_fold
    from .names import merge
    from .order import program

    expr = parse_expr(expression)
    identity = canonical(expr)
    tree = merge(library, diagnostics)
    folded = expand_and_fold(program(library, tree, expr), identity, diagnostics)
    return emit(folded, tree, identity=identity, layout=layout, diagnostics=diagnostics)


def compose(
    expression: str,
    roots: Sequence[Root],
    *,
    layout: ProjectLayout,
    diagnostics: list[Diagnostic],
) -> CompositionPlan:
    """The whole of processing for one CLI expression over the candidate roots."""
    return plan_from_library(
        read_roots(roots, diagnostics), expression, layout=layout, diagnostics=diagnostics
    )
