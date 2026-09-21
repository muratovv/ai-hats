"""The expression grammar — one grammar in three homes (ADR-0034 D2):
``expr := [op] term (op term)*``, an operator its own whitespace-separated
token, a term a suffix of a full name. A base is optional, a repeat is legal,
the order of ``+``/``-`` is kept; what a term may name is the resolver's call."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ProcessingError

_OPS = {"+": +1, "-": -1}
_SEGMENT = r"[A-Za-z0-9_.-]+"
_NAME = re.compile(rf"^{_SEGMENT}(::{_SEGMENT})*$")


@dataclass(frozen=True)
class Term:
    sign: int
    name: str


@dataclass(frozen=True)
class Expr:
    terms: tuple[Term, ...]
    #: The author wrote a sign before the first term: the expression appends to
    #: what lies below it instead of replacing it (ADR-0035 D3).
    leading: bool


def parse_expr(text: str) -> Expr:
    tokens = text.split()
    if not tokens:
        raise ProcessingError("empty expression")
    terms: list[Term] = []
    sign: int | None = None
    leading = tokens[0] in _OPS
    for token in tokens:
        if token in _OPS:
            if sign is not None:
                raise ProcessingError(f"two operators in a row in {text!r}")
            sign = _OPS[token]
            continue
        if "+" in token:
            raise ProcessingError(
                f"{token!r}: '+' is an operator and must be a separate token, spaced on both sides"
            )
        if not _NAME.match(token):
            raise ProcessingError(
                f"{token!r} is not a name (segments of [A-Za-z0-9_.-] joined by ::)"
            )
        if sign is None and terms:
            raise ProcessingError(f"missing operator before {token!r} in {text!r}")
        terms.append(Term(sign if sign is not None else +1, token))
        sign = None
    if sign is not None:
        raise ProcessingError(f"expression ends with an operator: {text!r}")
    return Expr(terms=tuple(terms), leading=leading)


def canonical(expr: Expr) -> str:
    """The one spelling of an expression: single spaces, operators as tokens,
    a leading sign only where the author wrote one."""
    parts: list[str] = []
    for i, term in enumerate(expr.terms):
        op = "+" if term.sign > 0 else "-"
        if i == 0 and not expr.leading:
            parts.append(term.name)
        else:
            parts.append(f"{op} {term.name}")
    return " ".join(parts)
