"""matching.py: the shared case-sensitive fnmatch matcher over a SEQUENCE of
globs (OR-combined) used by both ``context --with`` and ``ls --link``
(HATS-1029; HATS-1032 repeatable flags replace the home-grown ``|`` DSL)."""

from __future__ import annotations

from ai_hats_rack.matching import compile_matcher, matches


def test_single_glob_prefix():
    m = compile_matcher(("plan*",))
    assert m("plan.md") and m("plan_v2.md")
    assert not m("summary.md")


def test_multiple_globs_match_any_branch():
    m = compile_matcher(("plan*", "summary*"))
    assert m("plan.md") and m("summary.md")
    assert not m("notes.md")


def test_suffix_glob():
    m = compile_matcher(("*.log",))
    assert m("build.log") and not m("build.txt")


def test_exact_name_only_matches_itself():
    m = compile_matcher(("notes.md",))
    assert m("notes.md") and not m("notes.markdown")


def test_whitespace_around_globs_is_trimmed():
    m = compile_matcher(("  plan*  ", "  summary*  "))
    assert m("plan.md") and m("summary.md")


def test_empty_sequence_matches_nothing():
    assert compile_matcher(())("anything") is False


def test_all_blank_globs_match_nothing():
    # A set of only-blank globs must NOT collapse into a silent match-all.
    assert compile_matcher(("", " ", "  "))("plan.md") is False


def test_matching_is_case_sensitive():
    # fnmatchcase, not fnmatch — deterministic across macOS/Linux.
    assert compile_matcher(("PLAN*",))("plan.md") is False
    assert compile_matcher(("plan*",))("PLAN.md") is False


def test_question_mark_and_char_class():
    assert compile_matcher(("plan.??",))("plan.md") is True
    assert compile_matcher(("[ps]*",))("plan.md") and compile_matcher(("[ps]*",))("summary.md")
    assert not compile_matcher(("[ps]*",))("notes.md")


def test_pipe_is_a_literal_char_not_an_alternation():
    # HATS-1032: the home-grown `|` DSL is gone — a `|` inside a glob is now a
    # literal character (the repeatable flag carries the OR).
    m = compile_matcher(("plan*|summary*",))
    assert not m("plan.md") and not m("summary.md")
    assert m("plan-x|summary-y")  # matches only because the literal pipe is present


def test_matches_convenience():
    assert matches(("plan*", "summary*"), "summary.md") is True
    assert matches(("plan*",), "notes.md") is False
