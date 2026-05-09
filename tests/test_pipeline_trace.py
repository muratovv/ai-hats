"""Tests for pipeline trace mode (HATS-274).

Covers:
  - per-step event emission with correct keys / values / duration
  - error capture for halt-failure and continue-failure
  - JSONL writer formatting + flush-on-failure
  - hook isolation (a writer raising never aborts the pipeline)
  - safe_repr truncation
  - zero-overhead path when on_step is None
"""

from __future__ import annotations

import json
import time

import pytest

from ai_hats.pipeline.pipeline import build, run as run_pipeline
from ai_hats.pipeline.step import Step, StepIO
from ai_hats.pipeline.trace import (
    JsonlTraceWriter,
    TraceEvent,
    make_event,
    safe_repr,
)


# ---- helper steps ----------------------------------------------------


class _AddOneStep(Step):
    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(name="add_one", requires=frozenset({"x"}), produces=frozenset({"y"}))

    def run(self, *, x, **_):
        return {"y": x + 1}


class _DoubleStep(Step):
    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(name="double", requires=frozenset({"y"}), produces=frozenset({"z"}))

    def run(self, *, y, **_):
        return {"z": y * 2}


class _SlowStep(Step):
    """Sleeps 10ms — used to assert duration is measured."""

    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(name="slow", produces=frozenset({"slept"}))

    def run(self, **_):
        time.sleep(0.01)
        return {"slept": True}


class _EchoStep(Step):
    """Passes ``payload`` through unchanged — used to verify trace_values
    works on long-string inputs."""

    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(name="echo", requires=frozenset({"payload"}), produces=frozenset({"echoed"}))

    def run(self, *, payload, **_):
        return {"echoed": payload}


class _BoomStep(Step):
    """Raises on every run."""

    failure_policy = "halt"

    @property
    def io(self) -> StepIO:
        return StepIO(name="boom", produces=frozenset({"never"}))

    def run(self, **_):
        raise RuntimeError("kaboom")


class _BoomContinueStep(_BoomStep):
    failure_policy = "continue"


# ---- safe_repr -------------------------------------------------------


def test_safe_repr_short_value_unchanged():
    assert safe_repr(42) == "42"
    assert safe_repr("ping") == "'ping'"


def test_safe_repr_truncates_long_strings():
    long = "x" * 500
    s = safe_repr(long)
    assert "[+" in s and "more chars]" in s
    assert len(s) < 200  # truncated, not full 500


# ---- TraceEvent / make_event ----------------------------------------


def test_make_event_keys_only_by_default():
    ev = make_event(
        "demo",
        {"a": 1, "b": "long"},
        {"out": [1, 2, 3]},
        12.34,
    )
    assert ev.step == "demo"
    assert ev.requires_seen == ["a", "b"]
    assert ev.produces == ["out"]
    assert ev.duration_ms == 12.34
    assert ev.error is None
    assert ev.requires_values is None
    assert ev.produces_values is None


def test_make_event_with_values_truncates():
    long_input = "x" * 500
    ev = make_event(
        "demo",
        {"prompt": long_input},
        {"out": "ok"},
        1.0,
        include_values=True,
    )
    assert ev.requires_values is not None
    assert ev.produces_values is not None
    assert "[+" in ev.requires_values["prompt"]
    assert ev.produces_values["out"] == "'ok'"


def test_make_event_records_error_string():
    err = ValueError("bad input")
    ev = make_event("demo", {}, {}, 0.5, error=err)
    assert ev.error == "ValueError: bad input"


# ---- _run_steps emits events ----------------------------------------


def test_trace_emits_event_per_step():
    captured: list[TraceEvent] = []
    p = build(_AddOneStep(), _DoubleStep(), name="t")

    final = run_pipeline(p, {"x": 3}, on_step=captured.append)

    assert final["z"] == 8
    assert [e.step for e in captured] == ["add_one", "double"]


def test_trace_records_requires_and_produces_keys():
    captured: list[TraceEvent] = []
    p = build(_AddOneStep(), _DoubleStep())
    run_pipeline(p, {"x": 1}, on_step=captured.append)

    add = captured[0]
    assert add.requires_seen == ["x"]
    assert add.produces == ["y"]
    double = captured[1]
    assert double.requires_seen == ["y"]
    assert double.produces == ["z"]


def test_trace_records_duration_realistic():
    captured: list[TraceEvent] = []
    p = build(_SlowStep())
    run_pipeline(p, {}, on_step=captured.append)

    # SlowStep sleeps 10ms; assert at least 5ms to absorb scheduler jitter.
    assert captured[0].duration_ms >= 5.0


def test_trace_records_halt_failure_and_reraises():
    captured: list[TraceEvent] = []
    p = build(_AddOneStep(), _BoomStep())

    with pytest.raises(RuntimeError, match="kaboom"):
        run_pipeline(p, {"x": 1}, on_step=captured.append)

    # Both events were captured BEFORE the re-raise.
    assert [e.step for e in captured] == ["add_one", "boom"]
    assert captured[0].error is None
    assert captured[1].error == "RuntimeError: kaboom"


def test_trace_records_continue_failure_and_proceeds():
    captured: list[TraceEvent] = []
    p = build(_BoomContinueStep(), _AddOneStep())

    # _AddOneStep needs `x` from initial — boom contributes nothing,
    # but continue-policy lets the pipeline keep going.
    final = run_pipeline(p, {"x": 5}, on_step=captured.append)

    assert final["y"] == 6
    assert [e.step for e in captured] == ["boom", "add_one"]
    assert captured[0].error == "RuntimeError: kaboom"
    assert captured[1].error is None


def test_trace_values_off_by_default():
    captured: list[TraceEvent] = []
    p = build(_AddOneStep())
    run_pipeline(p, {"x": 9}, on_step=captured.append)
    assert captured[0].requires_values is None
    assert captured[0].produces_values is None


def test_trace_values_on_includes_truncated_repr():
    captured: list[TraceEvent] = []
    p = build(_EchoStep())
    run_pipeline(
        p, {"payload": "y" * 500},
        on_step=captured.append, trace_values=True,
    )
    ev = captured[0]
    assert ev.requires_values is not None
    assert "[+" in ev.requires_values["payload"]
    assert ev.produces_values is not None
    assert "[+" in ev.produces_values["echoed"]


def test_trace_hook_failure_does_not_abort_pipeline(caplog):
    """Writer raising must not propagate."""

    def angry_hook(_event):
        raise OSError("disk full")

    p = build(_AddOneStep(), _DoubleStep())
    final = run_pipeline(p, {"x": 4}, on_step=angry_hook)
    assert final["z"] == 10  # pipeline finished despite hook failure


# ---- JsonlTraceWriter ------------------------------------------------


def test_jsonl_writer_appends_one_line_per_event(tmp_path):
    path = tmp_path / "trace.jsonl"
    writer = JsonlTraceWriter(path)
    writer(TraceEvent(ts="t1", step="a", produces=["x"]))
    writer(TraceEvent(ts="t2", step="b", produces=["y"]))

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["step"] == "a"
    assert parsed[1]["step"] == "b"


def test_jsonl_writer_flushes_on_failure(tmp_path):
    """Even if a halt-failure aborts the pipeline mid-flight, every
    completed/failed step's event is already on disk."""
    path = tmp_path / "trace.jsonl"
    writer = JsonlTraceWriter(path)
    p = build(_AddOneStep(), _BoomStep())

    with pytest.raises(RuntimeError):
        run_pipeline(p, {"x": 0}, on_step=writer)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[1]["error"].startswith("RuntimeError")


def test_jsonl_writer_creates_parent_dir(tmp_path):
    """Constructor should mkdir -p the parent."""
    path = tmp_path / "nested" / "deeper" / "trace.jsonl"
    writer = JsonlTraceWriter(path)
    writer(TraceEvent(ts="t1", step="a"))
    assert path.exists()


def test_jsonl_writer_swallows_disk_errors(tmp_path, monkeypatch):
    """Failure to write must not propagate; logged at WARN."""
    path = tmp_path / "trace.jsonl"
    writer = JsonlTraceWriter(path)

    def boom(*a, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr("builtins.open", boom)
    # Must not raise.
    writer(TraceEvent(ts="t1", step="a"))


# ---- zero-overhead path ---------------------------------------------


def test_no_trace_when_on_step_is_none():
    """Sanity: pipeline runs without hook, no error, no allocation."""
    p = build(_AddOneStep(), _DoubleStep())
    final = run_pipeline(p, {"x": 2})  # no on_step kwarg
    assert final["z"] == 6
