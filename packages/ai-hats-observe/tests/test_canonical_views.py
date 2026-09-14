"""Projections over the event stream (HATS-1966)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_hats_observe.canonical import (
    ANSWER_ONLY,
    ItemEmitted,
    ItemKind,
    PromptReceived,
    REORDER_DEPTH,
    ResponseEnded,
    ResponseStarted,
    TextItem,
    ThinkingItem,
    Usage,
    Completion,
    in_time_order,
    select,
)


@dataclass(frozen=True)
class _Stamped:
    ts: str


def _order(events):
    return [e.ts for e in events if getattr(e, "ts", None)]


def test_a_bounded_reorder_restores_time_order() -> None:
    """The reader emits causally: a response closes only once something later
    proves it ended, so its end carries an earlier stamp than the tool results
    that arrived while it was open. The disorder is bounded by position, so a
    few events of buffer is enough.

    Positive control: the input really is out of order, so a pass-through
    implementation could not satisfy this.
    """
    scrambled = [_Stamped(t) for t in ("03", "05", "04", "07", "06", "08")]
    assert _order(scrambled) != sorted(_order(scrambled)), "fixture is not scrambled"

    ordered = list(in_time_order(scrambled))

    assert _order(ordered) == sorted(_order(scrambled))
    assert len(ordered) == len(scrambled), "reordering must not drop or duplicate"


def test_an_event_without_a_timestamp_keeps_the_place_it_arrived_in() -> None:
    """A source that reports no time — the SDK stream carries none — must pass
    through where it sits, not be flung to the front by a missing key."""

    @dataclass(frozen=True)
    class _Untimed:
        ts: None = None

    stream = [_Stamped("01"), _Untimed(), _Stamped("02")]
    ordered = list(in_time_order(stream, depth=2))

    assert [type(e).__name__ for e in ordered] == ["_Stamped", "_Untimed", "_Stamped"]


def test_disorder_deeper_than_the_buffer_is_not_silently_dropped() -> None:
    """The guarantee is bounded, and the bound is honest: an event later than
    the buffer can hold still comes out, just not in order. Nothing vanishes."""
    far = [_Stamped(f"{n:02d}") for n in (10, 11, 12, 13, 14, 1)]

    ordered = list(in_time_order(far, depth=2))

    assert len(ordered) == len(far)
    assert {e.ts for e in ordered} == {e.ts for e in far}


def test_a_projection_narrows_items_but_never_hides_a_signal() -> None:
    """A judge reads the answer without the reasoning — but a narrowed read must
    not make a run that was killed look like one that finished."""
    from ai_hats_observe.canonical import HarnessActionRequired, HarnessMustAct

    stream = [
        PromptReceived(text="go"),
        ResponseStarted(response_id="r1"),
        ItemEmitted("r1", ThinkingItem(text="why")),
        ItemEmitted("r1", TextItem(text="answer")),
        ResponseEnded("r1", Completion.COMPLETE, Usage(1, 2, 0, 0)),
        HarnessActionRequired(reason=HarnessMustAct.WAIT, retry_after=1),
    ]

    kept = list(select(stream, ANSWER_ONLY))

    kinds = [e.item.kind for e in kept if isinstance(e, ItemEmitted)]
    assert ItemKind.THINKING not in kinds
    assert ItemKind.TEXT in kinds
    assert any(isinstance(e, HarnessActionRequired) for e in kept)


def test_the_default_depth_clears_the_worst_disorder_measured() -> None:
    """2 places was the deepest disorder over 693 transcripts; the default keeps
    headroom over it rather than sitting exactly on the observed maximum."""
    assert REORDER_DEPTH >= 2 * 2


def test_a_call_announced_twice_is_still_billed_once() -> None:
    """A session read from several transcript files announces one call more than
    once. Folding must recognise it, or every such session is double-billed.

    Positive control: two genuinely distinct calls still count as two, so the
    guard cannot pass by collapsing everything into one.
    """
    from ai_hats_observe.canonical import collect

    twice = collect(
        [
            ResponseStarted(response_id="r1"),
            ResponseEnded("r1", Completion.COMPLETE, Usage(10, 5, 0, 0)),
            ResponseStarted(response_id="r1"),
            ResponseEnded("r1", Completion.COMPLETE, Usage(10, 5, 0, 0)),
        ]
    )
    assert twice.api_calls == 1
    assert twice.usage.input_tokens + twice.usage.output_tokens == 15

    # POSITIVE CONTROL: distinct ids are not collapsed
    distinct = collect(
        [
            ResponseStarted(response_id="r1"),
            ResponseEnded("r1", Completion.COMPLETE, Usage(10, 5, 0, 0)),
            ResponseStarted(response_id="r2"),
            ResponseEnded("r2", Completion.COMPLETE, Usage(10, 5, 0, 0)),
        ]
    )
    assert distinct.api_calls == 2
    assert distinct.usage.input_tokens + distinct.usage.output_tokens == 30


def test_a_prompt_reaches_the_fold() -> None:
    """``Collected.prompts`` was declared and never filled, so a consumer asking
    the fold for the dialogue got silence rather than an error."""
    from ai_hats_observe.canonical import PromptReceived, collect

    assert collect([PromptReceived(text="go"), PromptReceived(text="again")]).prompts == [
        "go",
        "again",
    ]
