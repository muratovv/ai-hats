"""One value, one spelling: what ``PipelineResult`` types is not also in ``produced``.

HATS-1783. ``exit_code`` and ``session`` used to be typed fields *and* raw entries in
``produced``, so a caller could read either — and the next migration standardises on
whichever one it happens to meet. ``from_state`` now lifts the keys the contract
answers for out of the funnel instead of copying them beside it, and these tests hold
that: the pinned field list is the review event, and the lift is the mechanism.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from ai_hats.pipeline import PipelineResult

#: Every value the area answers for itself. Adding one is a contract change
#: (ADR-0026 D14) and it must be lifted out of ``produced`` in the same edit,
#: or the value it names is readable two ways again.
PINNED_TYPED_FIELDS = {"session", "errors"}


def _state() -> dict[str, object]:
    """A final funnel with all four lifted keys plus what stays behind."""
    return {
        "session_id": "s-1",
        "session_dir": Path("/sessions/s-1"),
        "claude_session_id": "prov-1",
        "errors": {"launch": RuntimeError("boom")},
        "exit_code": 3,
        "review_path": Path("/sessions/s-1/review.md"),
    }


def test_the_typed_fields_are_the_pinned_ones() -> None:
    """A field added here is a second spelling until its key leaves ``produced``."""
    typed = {f.name for f in fields(PipelineResult)} - {"produced"}
    assert typed == PINNED_TYPED_FIELDS, (
        f"PipelineResult's typed fields are {sorted(typed)}, pinned "
        f"{sorted(PINNED_TYPED_FIELDS)}. A new one needs its funnel key added to "
        "``contract._TYPED_HERE`` so ``from_state`` lifts it out of ``produced`` — "
        "otherwise the same value is readable as a field and as a raw entry, and "
        "the next caller picks one at random (docs/how-to-extract-an-area.md §2)."
    )


def test_from_state_lifts_the_typed_keys_out_of_the_funnel() -> None:
    result = PipelineResult.from_state(_state())

    assert result.session is not None
    assert (result.session.id, result.session.provider_session_id) == ("s-1", "prov-1")
    assert set(result.errors) == {"launch"}
    lifted = {"session_id", "session_dir", "claude_session_id", "errors"}
    assert lifted.isdisjoint(result.produced), (
        f"{sorted(lifted & set(result.produced))} is readable both as a field and "
        "through ``produced``"
    )


def test_from_state_leaves_everything_else_where_its_step_wrote_it() -> None:
    """Including the exit code: the area carries it, ``session_policy`` reads it."""
    produced = PipelineResult.from_state(_state()).produced

    assert produced["exit_code"] == 3
    assert produced["review_path"] == Path("/sessions/s-1/review.md")


def test_a_run_without_a_session_answers_none_rather_than_half_a_session() -> None:
    result = PipelineResult.from_state({"session_id": "s-1", "exit_code": 0})

    assert result.session is None
    assert result.errors == {}
    assert result.produced == {"exit_code": 0}
