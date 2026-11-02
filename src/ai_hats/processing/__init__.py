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


def _optional_expr(text: str | None):
    return parse_expr(text) if text else None


def plan_from_library(
    library: Library,
    expression: str | None,
    *,
    layout: ProjectLayout,
    diagnostics: list[Diagnostic],
    default: str | None = None,
    active: str | None = None,
) -> CompositionPlan:
    """The stages after the read, in order: names, order, fold, prompt, emit."""
    from .emit import emit
    from .fold import expand_and_fold
    from .names import merge
    from .order import program

    tree = merge(library, diagnostics)
    prog = program(
        library,
        tree,
        _optional_expr(expression),
        default=_optional_expr(default),
        active=_optional_expr(active),
    )
    identity = canonical(prog.top)
    folded = expand_and_fold(prog, identity, diagnostics)
    return emit(folded, tree, identity=identity, layout=layout, diagnostics=diagnostics)


def compose(
    expression: str | None,
    roots: Sequence[Root],
    *,
    layout: ProjectLayout,
    diagnostics: list[Diagnostic],
    default: str | None = None,
    active: str | None = None,
) -> CompositionPlan:
    """The whole of processing for one CLI expression over the candidate roots."""
    return plan_from_library(
        read_roots(roots, diagnostics),
        expression,
        layout=layout,
        diagnostics=diagnostics,
        default=default,
        active=active,
    )
