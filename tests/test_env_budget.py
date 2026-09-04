"""The contract every numeric knob in this package obeys (HATS-1872).

Five of the six readers this replaces already fell back on an unusable value and
said so in a docstring; the sixth raised ``ValueError`` and took the harness with
it. The contract was real and written nowhere, so nothing could notice the one
that broke it.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import pytest

from ai_hats.env import Budget, read_budget

_SAMPLE = Budget("AI_HATS_TEST_BUDGET", 7.5, "a sample budget")
_WHOLE = Budget("AI_HATS_TEST_WHOLE", 10, "a sample count")


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "-1", "0", "nan", "inf", "1.2.3", "  "],
    ids=["empty", "garbage", "negative", "zero", "nan", "inf", "malformed", "blank"],
)
def test_an_unusable_value_falls_back_and_never_raises(raw: str) -> None:
    assert read_budget(_SAMPLE, {_SAMPLE.name: raw}) == _SAMPLE.default


def test_an_absent_name_is_the_default() -> None:
    assert read_budget(_SAMPLE, {}) == _SAMPLE.default


def test_a_usable_override_wins() -> None:
    assert read_budget(_SAMPLE, {_SAMPLE.name: "12.5"}) == 12.5


def test_a_whole_budget_stays_whole() -> None:
    """``keep_n`` indexes a slice, so a float override would be a TypeError
    deferred to whichever call site sliced with it."""
    got = read_budget(_WHOLE, {_WHOLE.name: "3"})
    assert got == 3
    assert isinstance(got, int)


def test_a_fractional_override_of_a_whole_budget_falls_back() -> None:
    """Not a rounding decision to make on the caller's behalf — unusable is
    unusable, and the default is the one answer that cannot surprise."""
    assert read_budget(_WHOLE, {_WHOLE.name: "3.5"}) == _WHOLE.default


def test_the_pipeline_rotation_survives_a_garbage_budget(tmp_path, monkeypatch) -> None:
    """The sixth reader. Its five siblings fall back on `abc`; this one parsed
    with a bare `int()` and took the harness down with it (HATS-1872 §1)."""
    from ai_hats.pipeline.harness import PipelineHarness

    monkeypatch.setenv("AI_HATS_PIPELINE_KEEP_N", "abc")
    harness = PipelineHarness("execute", ProjectLayout.at(tmp_path), session_id="sid")
    with harness:
        assert harness.namespace.is_dir()
