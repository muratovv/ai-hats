"""Retrofit timeout & cooperative cancellation in the pipeline runner.

Covers the HATS-584 additive contract on the existing StepIO model:
- Step.timeout / Step.on_cancel defaults (additive, opt-in).
- run() bounding a step in a worker thread on timeout -> PipelineCancelled.
- cooperative propagation: remaining steps skipped after a timeout.
- on_cancel cleanup invoked + partial delta merged (None-filter).
- existing FAILED / BuildError paths unchanged.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any

import pytest

from ai_hats.pipeline import (
    BuildError,
    CancelReason,
    CancelToken,
    PipelineCancelled,
    build,
    run,
)
from ai_hats.pipeline.step import Step, StepIO


class _NoopStep(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="noop")

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        return {}


class _SleepStep(Step):
    """Sleeps ``sleep_s`` then emits ``{out_key: out_val}`` (or nothing)."""

    def __init__(
        self,
        name: str,
        sleep_s: float,
        *,
        timeout: float | None = None,
        out_key: str | None = None,
        out_val: Any = "ok",
    ) -> None:
        self._name = name
        self._sleep = sleep_s
        self.timeout = timeout
        self._out_key = out_key
        self._out_val = out_val

    @property
    def io(self) -> StepIO:
        produces = frozenset({self._out_key}) if self._out_key else frozenset()
        return StepIO(name=self._name, produces=produces)

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        time.sleep(self._sleep)
        return {self._out_key: self._out_val} if self._out_key else {}


class _RecorderStep(Step):
    """Appends to ``log`` when run — used to prove it was skipped."""

    def __init__(self, log: list[str], out_key: str = "rec") -> None:
        self._log = log
        self._out_key = out_key

    @property
    def io(self) -> StepIO:
        return StepIO(name="recorder", produces=frozenset({self._out_key}))

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        self._log.append("ran")
        return {self._out_key: True}


class _FailStep(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="boom")

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        raise ValueError("boom")


class _NeedsStep(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="needs", requires=frozenset({"missing"}))

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        return {}


class _OnCancelStep(Step):
    """Always times out; ``on_cancel`` behaviour is configurable."""

    def __init__(
        self,
        name: str,
        *,
        timeout: float,
        sleep_s: float,
        produces: set[str],
        on_cancel_delta: dict[str, Any] | None = None,
        on_cancel_raises: bool = False,
    ) -> None:
        self._name = name
        self.timeout = timeout
        self._sleep = sleep_s
        self._produces = frozenset(produces)
        self._delta = on_cancel_delta
        self._raises = on_cancel_raises

    @property
    def io(self) -> StepIO:
        return StepIO(name=self._name, produces=self._produces)

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        time.sleep(self._sleep)
        return {}

    def on_cancel(self, **inputs: Any) -> dict[str, Any] | None:
        del inputs
        if self._raises:
            raise RuntimeError("cleanup boom")
        return self._delta


# --- S2: additive Step contract defaults ------------------------------------


def test_default_step_has_no_timeout() -> None:
    assert _NoopStep().timeout is None


def test_default_on_cancel_is_noop() -> None:
    assert _NoopStep().on_cancel() is None


# --- S3: thread-bounded timeout + cooperative propagation -------------------


def test_step_under_timeout_runs_normally() -> None:
    p = build(_SleepStep("fast", 0.0, timeout=1.0, out_key="x"))
    assert run(p, {}) == {"x": "ok"}


def test_step_over_timeout_raises_pipeline_cancelled() -> None:
    p = build(_SleepStep("slow", 0.3, timeout=0.05, out_key="x"))
    with pytest.raises(PipelineCancelled) as ei:
        run(p, {})
    assert ei.value.reason is CancelReason.TIMEOUT


def test_remaining_steps_skipped_after_timeout() -> None:
    log: list[str] = []
    p = build(
        _SleepStep("slow", 0.3, timeout=0.05, out_key="x"),
        _RecorderStep(log),
    )
    with pytest.raises(PipelineCancelled) as ei:
        run(p, {})
    assert log == []  # the recorder step never ran
    assert "rec" not in ei.value.state


def test_failing_step_reraises_not_cancelled() -> None:
    p = build(_FailStep())
    with pytest.raises(ValueError, match="boom"):
        run(p, {})


def test_builderror_path_unchanged() -> None:
    p = build(_NeedsStep())
    with pytest.raises(BuildError):
        run(p, {})


# --- S4: on_cancel cleanup invocation + partial-delta merge -----------------


def test_on_cancel_partial_delta_merged_on_timeout() -> None:
    step = _OnCancelStep(
        "slow", timeout=0.05, sleep_s=0.3,
        produces={"x"}, on_cancel_delta={"x": "partial"},
    )
    with pytest.raises(PipelineCancelled) as ei:
        run(build(step), {})
    assert ei.value.state["x"] == "partial"


def test_on_cancel_undeclared_keys_dropped() -> None:
    step = _OnCancelStep(
        "slow", timeout=0.05, sleep_s=0.3,
        produces={"x"}, on_cancel_delta={"y": 1},
    )
    with pytest.raises(PipelineCancelled) as ei:
        run(build(step), {})
    assert "y" not in ei.value.state


def test_on_cancel_none_value_filtered() -> None:
    step = _OnCancelStep(
        "slow", timeout=0.05, sleep_s=0.3,
        produces={"x"}, on_cancel_delta={"x": None},
    )
    with pytest.raises(PipelineCancelled) as ei:
        run(build(step), {})
    assert "x" not in ei.value.state


def test_on_cancel_raise_is_swallowed() -> None:
    step = _OnCancelStep(
        "slow", timeout=0.05, sleep_s=0.3,
        produces={"x"}, on_cancel_raises=True,
    )
    # Cleanup raising must not crash the cancellation path.
    with pytest.raises(PipelineCancelled) as ei:
        run(build(step), {})
    assert ei.value.reason is CancelReason.TIMEOUT


# --- External cancellation (caller-supplied token, IN SCOPE v1) -------------


def test_external_pre_cancelled_token_stops_before_first_step() -> None:
    log: list[str] = []
    token = CancelToken()
    token.cancel(CancelReason.EXTERNAL)
    with pytest.raises(PipelineCancelled) as ei:
        run(build(_RecorderStep(log)), {}, cancel_token=token)
    assert log == []
    assert ei.value.reason is CancelReason.EXTERNAL


def test_external_cancel_between_steps_skips_remainder() -> None:
    log: list[str] = []
    token = CancelToken()

    class _FlipStep(Step):
        @property
        def io(self) -> StepIO:
            return StepIO(name="flip")

        def run(self, **inputs: Any) -> dict[str, Any]:
            del inputs
            token.cancel(CancelReason.EXTERNAL)  # external thread analogue
            return {}

    with pytest.raises(PipelineCancelled) as ei:
        run(build(_FlipStep(), _RecorderStep(log)), {}, cancel_token=token)
    assert log == []  # second step skipped at the boundary
    assert ei.value.reason is CancelReason.EXTERNAL


# --- S5: process-group kill via on_cancel (real subprocess) -----------------
# Real-subprocess test is justified per dev_rule_e2e_gate: the contract under
# test (a pipeline timeout actually terminating a spawned process tree) IS at
# the subprocess boundary and cannot be exercised by a pure function.


class _SubprocessStep(Step):
    """Spawns ``sleep 30`` in its own session; on_cancel kills the group.

    ``run`` records the child's final ``returncode`` after ``proc.wait``
    returns (in the orphaned worker thread, post-kill) and signals ``done``,
    so the test can assert the process was actually SIGKILLed rather than
    racing on a pgid probe (which is prone to PID/pgid reuse).
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self._pgid: int | None = None
        self.returncode: int | None = None
        self.done = threading.Event()

    @property
    def io(self) -> StepIO:
        return StepIO(name="spawn", produces=frozenset({"pgid"}))

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self._pgid = os.getpgid(proc.pid)
        proc.wait()  # blocks in the worker thread until the group is killed
        self.returncode = proc.returncode
        self.done.set()
        return {"pgid": self._pgid}

    def on_cancel(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        # Kill by the stored pgid only — never touch the live Popen from
        # this thread while run() may still be inside proc.wait().
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return {"pgid": self._pgid}


def test_on_cancel_kills_process_group_on_timeout() -> None:
    step = _SubprocessStep(timeout=0.5)
    with pytest.raises(PipelineCancelled) as ei:
        run(build(step), {})
    assert ei.value.reason is CancelReason.TIMEOUT

    # The orphaned worker thread's proc.wait() returns once the group is
    # killed; assert the child was terminated by our SIGKILL (returncode is
    # the negated signal number per subprocess semantics).
    assert step.done.wait(timeout=5.0), "spawned process never terminated"
    assert step.returncode == -signal.SIGKILL
