"""The expression grammar (ADR-0034 D2): one grammar for a composite file, an
override file and the CLI, with the five amendments to today's role spec."""

from __future__ import annotations

import pytest

from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.expr import Expr, Term, canonical, parse_expr


def test_base_is_optional_and_order_between_plus_and_minus_is_kept():
    expr = parse_expr("maintainer - trait-agent + worker - devils-advocate")
    assert expr == Expr(
        terms=(
            Term(+1, "maintainer"),
            Term(-1, "trait-agent"),
            Term(+1, "worker"),
            Term(-1, "devils-advocate"),
        ),
        leading=False,
    )
    assert canonical(expr) == "maintainer - trait-agent + worker - devils-advocate"


def test_a_repeated_operand_parses():
    expr = parse_expr("A + B - A")
    assert [t.name for t in expr.terms] == ["A", "B", "A"]
    assert canonical(expr) == "A + B - A"


def test_a_leading_sign_is_kept_by_parse_and_canonicalization():
    expr = parse_expr("- devils-advocate + worker")
    assert expr.leading is True
    assert expr.terms == (Term(-1, "devils-advocate"), Term(+1, "worker"))
    assert canonical(expr) == "- devils-advocate + worker"
    assert parse_expr("+ worker").leading is True
    assert parse_expr("worker").leading is False


def test_canonicalization_normalizes_spacing_only():
    assert canonical(parse_expr("  maintainer   +   worker ")) == "maintainer + worker"
    assert canonical(parse_expr("maintainer\n  + dev::python")) == "maintainer + dev::python"


def test_a_minus_inside_a_name_is_a_name_character():
    expr = parse_expr("trait-base - trait-agent")
    assert expr.terms == (Term(+1, "trait-base"), Term(-1, "trait-agent"))


def test_any_kind_of_operand_is_grammatical():
    """``maintainer + sre`` and ``maintainer + rack.transition`` both parse —
    what a term may name is the resolver's refusal (D2), not the grammar's."""
    assert canonical(parse_expr("maintainer + sre")) == "maintainer + sre"
    assert canonical(parse_expr("maintainer + rack.transition")) == "maintainer + rack.transition"


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("maintainer +", "ends with an operator"),
        ("maintainer + + worker", "two operators"),
        ("maintainer worker", "missing operator"),
        ("maintainer+worker", "separate token"),
        ("maintainer + wor ker!", "not a name"),
        ("maintainer + ::x", "not a name"),
        ("maintainer + a::", "not a name"),
    ],
)
def test_a_malformed_expression_refuses_naming_the_fault(text: str, fragment: str):
    with pytest.raises(ProcessingError, match=fragment):
        parse_expr(text)
