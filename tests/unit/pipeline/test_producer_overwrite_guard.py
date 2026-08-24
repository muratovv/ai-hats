"""Lost-overwrite guard per HATS-1249.

Two steps producing the same state key are fine as long as each producer's
value is read before the next one overwrites it — that is the interleaved
shape, and it already worked. What was silent is the fan-out shape, where a
producer's output is overwritten before anything reads it: the value is simply
gone, with no error and no warning. The guard refuses exactly that, in step
order, at build time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_hats.pipeline.pipeline import BuildError, _check_overwrites, build
from ai_hats.pipeline.step import Step, StepIO


class _FakeStep(Step):
    def __init__(
        self,
        name: str,
        *,
        requires: frozenset[str] = frozenset(),
        optional: frozenset[str] = frozenset(),
        produces: frozenset[str] = frozenset(),
        delta: dict[str, Any] | None = None,
    ) -> None:
        self._io = StepIO(name=name, requires=requires, optional=optional, produces=produces)
        self._delta = delta or {}

    @property
    def io(self) -> StepIO:
        return self._io

    def run(self, **inputs: Any) -> dict[str, Any]:
        return dict(self._delta)


def _provider(tag: str) -> _FakeStep:
    return _FakeStep(
        f"provider_{tag}",
        produces=frozenset({"session_id", "exit_code"}),
        delta={"session_id": f"sess-{tag}", "exit_code": 0},
    )


# ---------- the two two-launch shapes ----------


def test_interleaved_shape_is_accepted_and_each_consumer_sees_its_own_session() -> None:
    seen: list = []

    class _Reader(_FakeStep):
        def run(self, *, session_id: str, **_: Any) -> dict[str, Any]:
            seen.append((self.io.name, session_id))
            return {}

    final = build(
        _provider("A"),
        _Reader("after_a", requires=frozenset({"session_id"})),
        _provider("B"),
        _Reader("after_b", requires=frozenset({"session_id"})),
    ).run()

    assert seen == [("after_a", "sess-A"), ("after_b", "sess-B")]
    assert final["session_id"] == "sess-B"


def test_fanout_shape_is_refused_naming_both_steps_and_the_lost_key() -> None:
    with pytest.raises(BuildError) as exc:
        build(
            _provider("A"),
            _provider("B"),
            _FakeStep("shared", requires=frozenset({"session_id"})),
        )

    message = str(exc.value)
    assert "provider_B" in message
    assert "provider_A" in message
    assert "session_id" in message


def test_two_producers_with_no_consumer_at_all_are_refused() -> None:
    """The first launch's exit_code would be lost — the card's other symptom."""
    with pytest.raises(BuildError):
        build(_provider("A"), _provider("B"))


# ---------- shapes that must stay legal ----------


def test_a_key_read_only_as_optional_counts_as_read() -> None:
    _check_overwrites(
        (
            _provider("A"),
            _FakeStep("peek", optional=frozenset({"session_id"})),
            _provider("B"),
        )
    )


def test_three_producers_are_fine_when_each_is_read() -> None:
    _check_overwrites(
        (
            _provider("A"),
            _FakeStep("r1", requires=frozenset({"session_id"})),
            _provider("B"),
            _FakeStep("r2", requires=frozenset({"session_id"})),
            _provider("C"),
            _FakeStep("r3", requires=frozenset({"session_id"})),
        )
    )


def test_a_step_that_reads_and_rewrites_one_key_is_fine() -> None:
    """The append-prompt shape: requires and produces the same key."""
    _check_overwrites(
        (
            _FakeStep("seed", produces=frozenset({"prompt"})),
            _FakeStep("append", requires=frozenset({"prompt"}), produces=frozenset({"prompt"})),
            _FakeStep("append2", requires=frozenset({"prompt"}), produces=frozenset({"prompt"})),
        )
    )


def test_a_single_producer_read_by_nobody_is_fine() -> None:
    """The CLI reads it out of the final state — that is not a lost overwrite."""
    _check_overwrites((_provider("A"),))


def test_a_key_from_initial_state_is_not_a_producer() -> None:
    _check_overwrites((_FakeStep("writes_seeded", produces=frozenset({"seeded"})),))


# ---------- every shipped pipeline stays legal ----------


def test_every_shipped_pipeline_is_accepted() -> None:
    from ai_hats.paths import core_pipeline_path
    from ai_hats.pipeline.loader import load_pipeline

    # Glob the directory rather than a hardcoded list, so a pipeline added
    # later cannot escape the guard silently.
    anchor = core_pipeline_path("execute")
    assert anchor is not None
    yamls = sorted(Path(anchor).parent.glob("*.yaml"))
    assert len(yamls) >= 12

    for path in yamls:
        _check_overwrites(load_pipeline(path).steps)
