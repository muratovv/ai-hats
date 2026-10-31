"""Expansion and fold (ADR-0035 D3, D4): a composite opens in place with its
sign multiplied in, ``+X`` is idempotent, ``-X`` removes wherever, the trace
says who brought and who removed; self-reference looks down the layers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.expr import parse_expr
from ai_hats.processing.fold import expand_and_fold
from ai_hats.processing.names import merge
from ai_hats.processing.order import program
from ai_hats.processing.roots import Root, read_roots
from ai_hats.surfaces.plan import TraceEntry
from tests._flat_library import write_root


def _prompt(text: str) -> str:
    return f"---\nschema_version: 1\ntype: prompt\nblock: body\n---\n{text}\n"


def _composite(expr: str) -> str:
    return f"schema_version: 1\ntype: composite\nexpr: {expr}\n"


OVERLAP = {
    "A.yaml": _composite("f1 + f2 + f3"),
    "B.yaml": _composite("f2 + f3 + f4"),
    "C.yaml": _composite("f1 - f2"),
    "f1.md": _prompt("F1"),
    "f2.md": _prompt("F2"),
    "f3.md": _prompt("F3"),
    "f4.md": _prompt("F4"),
}


def _fold(roots: list[Path], expr: str, diagnostics: list[Diagnostic] | None = None):
    sink = diagnostics if diagnostics is not None else []
    library = read_roots([Root(r) for r in roots], sink)
    return expand_and_fold(program(library, merge(library, sink), parse_expr(expr)), expr, sink)


@pytest.fixture
def overlap(tmp_path: Path) -> Path:
    return write_root(tmp_path / "lib", OVERLAP)


def test_a_plus_b_minus_a_keeps_only_what_a_never_had(overlap: Path):
    """The consequence accepted in D4: ``f2`` and ``f3`` go with ``A``."""
    diagnostics: list[Diagnostic] = []
    folded = _fold([overlap], "A + B - A", diagnostics)
    assert folded.members == ("f4",)
    assert [d.text for d in diagnostics] == [
        "'- A' (in A + B - A) removes f2, which B also brings",
        "'- A' (in A + B - A) removes f3, which B also brings",
    ]


def test_a_literal_minus_is_the_author_speaking_and_does_not_warn(overlap: Path):
    diagnostics: list[Diagnostic] = []
    assert _fold([overlap], "A + B - f2", diagnostics).members == ("f1", "f3", "f4")
    assert diagnostics == []


def test_plus_is_idempotent_and_minus_over_an_absent_term_is_a_no_op(overlap: Path):
    diagnostics: list[Diagnostic] = []
    folded = _fold([overlap], "f1 + A + f1 - f4 - f4", diagnostics)
    assert folded.members == ("f1", "f2", "f3")
    assert diagnostics == []


def test_a_negated_composite_flips_the_signs_of_its_terms(overlap: Path):
    """``- C`` with ``C = f1 - f2`` yields ``-f1 +f2``: ``+ A - A = 0`` for a
    composite of additions, while ``C - C`` folds ``+f1 -f2 -f1 +f2`` to ``f2``
    — the rule is total and the result reads off the string (D4)."""
    assert _fold([overlap], "A - A").members == ()
    assert _fold([overlap], "C - C").members == ("f2",)
    assert _fold([overlap], "f2 + C").members == ("f1",)
    assert _fold([overlap], "f2 - C").members == ("f2",), "-(f1 - f2) re-adds f2 (already there)"


def test_the_trace_names_who_brought_and_who_removed(overlap: Path):
    folded = _fold([overlap], "A + B - f3")
    assert folded.trace == (
        TraceEntry("A", "A + B - f3", None),
        TraceEntry("B", "A + B - f3", None),
        TraceEntry("f1", "A", None),
        TraceEntry("f2", "A", None),
        TraceEntry("f4", "B", None),
        TraceEntry("f3", "A", removed_by="A + B - f3"),
    )
    folded = _fold([overlap], "A + B - A")
    assert TraceEntry("f2", "A", removed_by="A + B - A") in folded.trace


def test_the_placing_occurrence_remembers_its_top_level_term(overlap: Path):
    folded = _fold([overlap], "B + A")
    assert folded.first_top_index == {"f2": 0, "f3": 0, "f4": 0, "f1": 1}
    folded = _fold([overlap], "B + A - f2 + f2")
    assert folded.first_top_index["f2"] == 3, "the move-to-tail idiom re-places it"
    assert folded.members == ("f3", "f4", "f1", "f2")


def test_self_reference_looks_down_the_layers(tmp_path: Path):
    lower = write_root(tmp_path / "lower", OVERLAP)
    upper = write_root(tmp_path / "upper", {"A.yaml": _composite("A + f4")})
    assert _fold([lower, upper], "A").members == ("f1", "f2", "f3", "f4")
    top = write_root(tmp_path / "top", {"A.yaml": _composite("A - f1")})
    assert _fold([lower, upper, top], "A").members == ("f2", "f3", "f4")


def test_a_self_reference_with_nothing_below_refuses(tmp_path: Path):
    only = write_root(tmp_path / "only", {**OVERLAP, "A.yaml": _composite("A + f4")})
    with pytest.raises(ProcessingError, match="names 'A' itself, but no layer below"):
        _fold([only], "A")


def test_a_cycle_through_another_name_refuses(tmp_path: Path):
    root = write_root(
        tmp_path / "lib",
        {**OVERLAP, "A.yaml": _composite("B + f1"), "B.yaml": _composite("A + f2")},
    )
    with pytest.raises(ProcessingError, match="cycle through 'A': A -> B -> A"):
        _fold([root], "A")


def test_an_unknown_name_refuses_under_either_sign(overlap: Path):
    with pytest.raises(ProcessingError, match="unknown name 'nope'"):
        _fold([overlap], "A + nope")
    with pytest.raises(ProcessingError, match="unknown name 'nope'"):
        _fold([overlap], "A - nope")


def test_a_leading_sign_with_nothing_below_refuses(overlap: Path):
    with pytest.raises(ProcessingError, match="starts with a sign, but there is nothing below"):
        _fold([overlap], "- f1 + A")
