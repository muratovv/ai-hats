"""matching.py: the shared fnmatch + ``|``-alternation matcher used by both
``context --with`` and ``ls --link`` (HATS-1029)."""

from __future__ import annotations

from ai_hats_rack.matching import alternatives, compile_matcher, matches


def test_single_glob_prefix():
    m = compile_matcher("plan*")
    assert m("plan.md") and m("plan_v2.md")
    assert not m("summary.md")


def test_alternation_matches_any_branch():
    m = compile_matcher("plan*|summary*")
    assert m("plan.md") and m("summary.md")
    assert not m("notes.md")


def test_suffix_glob():
    m = compile_matcher("*.log")
    assert m("build.log") and not m("build.txt")


def test_exact_name_only_matches_itself():
    m = compile_matcher("notes.md")
    assert m("notes.md") and not m("notes.markdown")


def test_whitespace_around_alternatives_is_trimmed():
    m = compile_matcher("  plan*  |  summary*  ")
    assert m("plan.md") and m("summary.md")


def test_empty_pattern_matches_nothing():
    assert compile_matcher("")("anything") is False


def test_all_empty_alternatives_match_nothing():
    # `|` with only blank branches must NOT collapse into a silent match-all.
    assert compile_matcher(" | | ")("plan.md") is False


def test_matching_is_case_sensitive():
    # fnmatchcase, not fnmatch — deterministic across macOS/Linux.
    assert compile_matcher("PLAN*")("plan.md") is False
    assert compile_matcher("plan*")("PLAN.md") is False


def test_question_mark_and_char_class():
    assert compile_matcher("plan.??")("plan.md") is True
    assert compile_matcher("[ps]*")("plan.md") and compile_matcher("[ps]*")("summary.md")
    assert not compile_matcher("[ps]*")("notes.md")


def test_alternatives_helper_strips_and_drops_blanks():
    assert alternatives(" plan* |  | summary* ") == ("plan*", "summary*")
    assert alternatives("") == ()


def test_matches_convenience():
    assert matches("plan*|summary*", "summary.md") is True
    assert matches("plan*", "notes.md") is False
