"""Contract guard: pipeline funnel drops ``None`` at the merge boundary.

HATS-452 / П3 in ADR-0005: the pipeline funnel value contract says
``None``-valued keys are equivalent to "key absent" — and the framework
enforces it by filtering ``None`` out of every step's delta before
merging into the shared context. This makes the empty-Optional-as-absent
trap that broke HATS-452 physically unreachable for any current or
future step author.

This file is intentionally minimal — it locks the merge-boundary
contract so a refactor of ``_run_steps`` cannot silently regress.
"""

from __future__ import annotations

from typing import Any

from ai_hats.pipeline.pipeline import build
from ai_hats.pipeline.step import Step, StepIO


class _Producer(Step):
    """Test fixture: emits both a None-valued and a value-valued key."""

    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="producer",
            requires=frozenset(),
            optional=frozenset(),
            produces=frozenset({"absent_key", "present_key", "empty_string_key"}),
        )

    def run(self, **_: Any) -> dict[str, Any]:
        return {
            "absent_key": None,        # MUST be filtered out
            "present_key": "hello",    # MUST flow through
            "empty_string_key": "",    # MUST flow through (valid value)
        }


class _Consumer(Step):
    """Test fixture: records what it actually saw in kwargs."""

    failure_policy = "halt"
    captured: dict[str, Any] = {}

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="consumer",
            requires=frozenset(),
            optional=frozenset({"absent_key", "present_key", "empty_string_key"}),
            produces=frozenset(),
        )

    def run(
        self,
        *,
        absent_key: Any = "DEFAULT_ABSENT",
        present_key: Any = "DEFAULT_PRESENT",
        empty_string_key: Any = "DEFAULT_EMPTY",
        **_: Any,
    ) -> dict[str, Any]:
        type(self).captured = {
            "absent_key": absent_key,
            "present_key": present_key,
            "empty_string_key": empty_string_key,
        }
        return {}


def test_funnel_drops_none_values_from_step_delta(tmp_path):
    """None-valued producer keys are filtered at the merge boundary;
    consumer sees them as absent (default value kicks in).

    Falsy non-None values (``""``) survive — П3 explicitly preserves
    them as legitimate values whose semantics differ from "absent".
    """
    del tmp_path
    _Consumer.captured = {}  # reset class attribute between runs
    pipeline = build(_Producer(), _Consumer(), name="test")
    final = pipeline.run()

    # State view: absent_key dropped, present_key + empty_string_key kept.
    assert "absent_key" not in final, (
        "HATS-452 regression: None-valued key leaked into context "
        "(should be dropped at funnel merge per П3)"
    )
    assert final["present_key"] == "hello"
    assert final["empty_string_key"] == ""  # falsy but NOT absent

    # Consumer view: absent_key got the function-signature default;
    # the other two arrived intact.
    cap = _Consumer.captured
    assert cap["absent_key"] == "DEFAULT_ABSENT", (
        "Consumer should fall back to default when producer emitted None; "
        "instead got: " + repr(cap["absent_key"])
    )
    assert cap["present_key"] == "hello"
    assert cap["empty_string_key"] == ""
