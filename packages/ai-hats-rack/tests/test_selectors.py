"""Selector grammar pins (HATS-1719, design.md §1.2-§1.4 on HATS-1684).

Derivability is wider than legality on purpose: the parser derives every arrow
so a refusal can say WHAT is wrong, and :func:`selector_form` narrows to what
this slice enables. A form reserved for a later card is refused BY NAME of that
card, so the word cannot be taken by someone else in the meantime.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.selectors import Edge, Selector, parse_selector


def test_exact_arrow_parses_to_its_pair():
    assert parse_selector("review->done") == Selector("review", "done")


@pytest.mark.parametrize(
    "edge, expected",
    [
        (Edge("review", "done"), True),
        (Edge("plan", "done"), False),
        (Edge("review", "execute"), False),
    ],
)
def test_exact_arrow_matches_only_its_own_pair(edge, expected):
    assert parse_selector("review->done").matches(edge) is expected


def test_a_name_without_an_arrow_is_not_ours():
    """``None`` is not an error: the checks DSL is shared by several apps."""
    assert parse_selector("pre-merge") is None


@pytest.mark.parametrize(
    "edge, expected",
    [
        (Edge("review", "done"), True),
        (Edge("brainstorm", "done"), True),
        (Edge("execute", "done"), True),
        (Edge("review", "execute"), False),
    ],
)
def test_wide_input_matches_every_road_into_the_state(edge, expected):
    """The measured hole: eight roads into ``done``, one of them gated."""
    assert parse_selector("->done").matches(edge) is expected


def test_wide_input_is_written_with_the_empty_side():
    assert str(parse_selector("->done")) == "->done"
    assert str(Selector("ANY", "done")) == "->done"


# --- legality: the §1.3 table, and the words a refusal must carry ------------


@pytest.mark.parametrize("text", ["review->done", "->done", "->execute"])
def test_legal_forms_are_accepted(text):
    from ai_hats_rack.selectors import selector_form

    assert selector_form(text) is None


@pytest.mark.parametrize(
    "text, must_say",
    [
        ("->", "both halves"),
        ("a->b->c", "exactly one"),
        ("review -> done", "whitespace"),
        ("reviewdone", "->"),
    ],
)
def test_malformed_forms_are_refused_saying_what_is_wrong(text, must_say):
    from ai_hats_rack.selectors import selector_form

    reason = selector_form(text)
    assert reason is not None and must_say in reason


@pytest.mark.parametrize("text", ["NONE->execute", "review->NONE"])
def test_none_is_reserved_and_names_the_card_that_opens_it(text):
    """A word in the catalog is a promise: reserved WITH the call site named."""
    from ai_hats_rack.selectors import selector_form

    reason = selector_form(text)
    assert reason is not None and "HATS-1703" in reason


@pytest.mark.parametrize("text", ["execute->", "ANY->ANY"])
def test_wide_output_is_refused_by_name_of_the_card_that_opens_it(text):
    """This slice enables the exact form and the wide INPUT; the wide output
    arrives with its veto rule, and until then the form must not half-work."""
    from ai_hats_rack.selectors import selector_form

    reason = selector_form(text)
    assert reason is not None and "HATS-1720" in reason


@pytest.mark.parametrize(
    "text, must_say",
    [
        # ``ANY`` exists to spell "everywhere" as ONE token, so on one side it is
        # merely a second spelling of the empty side — and two spellings of one
        # selector are two rows to the dedup key (``AppBinding.identity``).
        ("ANY->done", "'->done'"),
        ("review->ANY", "HATS-1720"),
    ],
)
def test_any_on_one_side_only_is_refused(text, must_say):
    from ai_hats_rack.selectors import selector_form

    reason = selector_form(text)
    assert reason is not None
    assert must_say in reason


# --- derivability is WIDER than legality, and that gap has a consumer --------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("execute->", Selector("execute", "ANY")),
        ("->done", Selector("ANY", "done")),
        ("ANY->ANY", Selector("ANY", "ANY")),
    ],
)
def test_the_parser_derives_forms_the_grammar_does_not_yet_allow(text, expected):
    """Pinned at the PARSE level on purpose (HATS-1719 review).

    ``checks.py`` calls ``parse_selector`` directly and never ``selector_form``:
    legality is enforced one layer up, at composition. ``CheckPort`` is a
    Protocol, so a port that is not ai-hats's own hands rows straight to the
    subscriber — and there ``execute->`` is a live wildcard over every way out.
    The retired parse table covered the half-empty shapes; this replaces it, and
    says which layer owes the refusal.
    """
    assert parse_selector(text) == expected


def test_a_derived_form_this_slice_does_not_allow_is_still_refused_by_the_judge():
    """The other half of the same fact, so the pair cannot drift apart."""
    from ai_hats_rack.selectors import selector_form

    assert selector_form("execute->") is not None
    assert parse_selector("execute->") is not None
