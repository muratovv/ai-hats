"""Tests for pipeline core per ADR-0001 §1-§3."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from ai_hats.pipeline import (
    BuildError,
    Pipeline,
    Step,
    StepError,
    StepIO,
    build,
    run,
)


# ---------- helpers ----------


class _FakeStep(Step):
    def __init__(
        self,
        name: str,
        *,
        requires: frozenset[str] = frozenset(),
        optional: frozenset[str] = frozenset(),
        produces: frozenset[str] = frozenset(),
        delta: dict[str, Any] | None = None,
        capture: list | None = None,
        failure_policy: str = "halt",
        raises: Exception | None = None,
    ) -> None:
        self._io = StepIO(name=name, requires=requires, optional=optional, produces=produces)
        self._delta = delta if delta is not None else {}
        self._capture = capture
        self.failure_policy = failure_policy  # type: ignore[assignment]
        self._raises = raises

    @property
    def io(self) -> StepIO:
        return self._io

    def run(self, **inputs: Any) -> dict[str, Any]:
        if self._capture is not None:
            self._capture.append((self._io.name, dict(inputs)))
        if self._raises is not None:
            raise self._raises
        return dict(self._delta)


# ---------- StepIO frozenness ----------


def test_step_io_is_frozen() -> None:
    io = StepIO(name="x")
    with pytest.raises(FrozenInstanceError):
        io.name = "y"  # type: ignore[misc]


# ---------- build / pipeline.io ----------


def test_build_accepts_chained_produces() -> None:
    a = _FakeStep("a", produces=frozenset({"x"}), delta={"x": 1})
    b = _FakeStep("b", requires=frozenset({"x"}))
    pipe = build(a, b, name="chain")
    assert pipe.io.requires == frozenset()
    assert pipe.io.produces == frozenset({"x"})


def test_pipeline_io_external_requires_aggregated() -> None:
    a = _FakeStep("a", requires=frozenset({"alpha"}), produces=frozenset({"x"}))
    b = _FakeStep("b", requires=frozenset({"x", "beta"}))
    pipe = build(a, b)
    # alpha and beta are external (not produced); x is satisfied internally.
    assert pipe.io.requires == frozenset({"alpha", "beta"})
    assert pipe.io.produces == frozenset({"x"})


def test_pipeline_io_optional_minus_produced_minus_required() -> None:
    # Step a produces x; step b lists {x, y} as optional.
    # x is produced upstream → not external. y stays as external optional.
    # Step c requires y → y is upgraded to external requires (not optional).
    a = _FakeStep("a", produces=frozenset({"x"}), delta={"x": 1})
    b = _FakeStep("b", optional=frozenset({"x", "y"}))
    pipe = build(a, b)
    assert pipe.io.requires == frozenset()
    assert pipe.io.optional == frozenset({"y"})


# ---------- run-time projection / threading ----------


def test_run_passes_only_projection() -> None:
    capture: list = []
    a = _FakeStep(
        "a",
        requires=frozenset({"req1"}),
        optional=frozenset({"opt1"}),
        capture=capture,
    )
    pipe = build(a)
    run(pipe, {"req1": "R", "opt1": "O", "extra": "E"})
    assert capture == [("a", {"req1": "R", "opt1": "O"})]


def test_run_omits_optional_when_absent() -> None:
    capture: list = []
    a = _FakeStep(
        "a",
        requires=frozenset({"req1"}),
        optional=frozenset({"opt1"}),
        capture=capture,
    )
    pipe = build(a)
    run(pipe, {"req1": "R"})
    assert capture == [("a", {"req1": "R"})]


def test_run_threads_state_between_steps() -> None:
    capture: list = []
    a = _FakeStep("a", produces=frozenset({"x"}), delta={"x": 42})
    b = _FakeStep("b", requires=frozenset({"x"}), capture=capture)
    pipe = build(a, b)
    run(pipe, {})
    assert capture == [("b", {"x": 42})]


def test_run_rejects_undeclared_requires() -> None:
    a = _FakeStep("a", requires=frozenset({"missing"}))
    pipe = build(a)
    with pytest.raises(BuildError, match="missing"):
        run(pipe, {})


# ---------- delta validation ----------


def test_run_validates_unexpected_delta_keys() -> None:
    a = _FakeStep(
        "a",
        produces=frozenset({"x"}),
        delta={"x": 1, "rogue": 2},
    )
    pipe = build(a)
    with pytest.raises(StepError, match="rogue"):
        run(pipe, {})


def test_run_allows_partial_delta_subset_of_produces() -> None:
    # Step declares produces={x, y} but only emits x — that's allowed.
    a = _FakeStep("a", produces=frozenset({"x", "y"}), delta={"x": 1})
    pipe = build(a)
    state = run(pipe, {})
    assert state["x"] == 1
    assert "y" not in state


# ---------- failure_policy ----------


def test_run_failure_policy_halt_propagates() -> None:
    boom = RuntimeError("boom")
    a = _FakeStep("a", failure_policy="halt", raises=boom)
    pipe = build(a)
    with pytest.raises(RuntimeError, match="boom"):
        run(pipe, {})


def test_run_failure_policy_continue_captures_and_advances() -> None:
    capture: list = []
    a = _FakeStep("a", failure_policy="continue", raises=ValueError("oops"))
    b = _FakeStep("b", capture=capture)
    pipe = build(a, b)
    state = run(pipe, {})
    assert "errors" in state
    assert isinstance(state["errors"]["a"], ValueError)
    assert capture == [("b", {})]


# ---------- runtime-absent required key ----------
# A producer may legally OMIT a declared ``produces`` key at runtime (ADR-0005
# value contract: None-filtered merge, ``ComposeRole`` returns ``{}`` for no
# role). Build-time validation trusts the *declared* produces, so it passes; the
# downstream ``requires`` projection then hits a missing key. That must surface
# as a typed ``StepError`` raised INSIDE the per-step try (so failure_policy and
# the trace hook apply), never as a bare ``KeyError`` that escapes both.


def test_run_missing_runtime_required_raises_step_error() -> None:
    producer = _FakeStep("producer", produces=frozenset({"k"}), delta={})
    consumer = _FakeStep("consumer", requires=frozenset({"k"}))
    pipe = build(producer, consumer)
    with pytest.raises(StepError) as exc:
        run(pipe, {})
    assert not isinstance(exc.value, KeyError)
    assert "consumer" in str(exc.value)
    assert "k" in str(exc.value)


def test_run_missing_runtime_required_continue_records_and_advances() -> None:
    events: list = []
    capture: list = []
    producer = _FakeStep("producer", produces=frozenset({"k"}), delta={})
    consumer = _FakeStep("consumer", requires=frozenset({"k"}), failure_policy="continue")
    tail = _FakeStep("tail", capture=capture)
    pipe = build(producer, consumer, tail)
    state = run(pipe, {}, on_step=lambda e: events.append(e))
    # continue-policy: error recorded, pipeline advanced past the failing step.
    assert isinstance(state["errors"]["consumer"], StepError)
    assert capture == [("tail", {})]
    # trace hook saw an error event for the consumer.
    consumer_events = [e for e in events if e.step == "consumer"]
    assert consumer_events and consumer_events[0].error is not None


# ---------- Pipeline is a Step (recursive) ----------


def test_pipeline_is_a_step_instance() -> None:
    pipe = build(_FakeStep("a"))
    assert isinstance(pipe, Step)
    assert isinstance(pipe, Pipeline)


# ---------- Homogenized Pipeline.run & Naming ----------


def test_pipeline_run_direct_call_validates_and_executes() -> None:
    a = _FakeStep(
        "a", requires=frozenset({"req1"}), produces=frozenset({"out1"}), delta={"out1": "val1"}
    )
    pipe = build(a, name="my_pipe")
    # Pre-flight check fails if required key missing
    with pytest.raises(BuildError, match="req1"):
        pipe.run()

    # Direct call with kwargs works
    res = pipe.run(req1="hello")
    assert res["out1"] == "val1"


def test_pipeline_run_accepts_initial_dict_and_kwargs() -> None:
    a = _FakeStep(
        "a", requires=frozenset({"k1", "k2"}), produces=frozenset({"out"}), delta={"out": "ok"}
    )
    pipe = build(a)
    res = pipe.run(initial={"k1": "v1"}, k2="v2")
    assert res["out"] == "ok"


def test_pipeline_name_backwards_compatibility_property_and_alias() -> None:
    pipe1 = Pipeline(steps=(), name="custom_name")
    assert pipe1.name == "custom_name"
    assert pipe1.pipeline_name == "custom_name"

    # Legacy constructor keyword
    pipe2 = Pipeline(steps=(), pipeline_name="legacy_name")  # type: ignore[call-arg]
    assert pipe2.name == "legacy_name"
    assert pipe2.pipeline_name == "legacy_name"


def test_pipeline_run_supports_on_step_and_cancel_token() -> None:
    events = []
    a = _FakeStep("a", produces=frozenset({"x"}), delta={"x": 10})
    pipe = build(a)
    res = pipe.run(on_step=lambda e: events.append(e))
    assert res["x"] == 10
    assert len(events) == 1
    assert events[0].step == "a"
