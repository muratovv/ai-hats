"""Tests for PreStub / PostStub — Phase 1 reference implementations."""

from __future__ import annotations

from typing import Any

import pytest

from ai_hats.pipeline import BuildError, Step, StepIO, build, run
from ai_hats.pipeline.steps.stubs import PostStub, PreStub


class _FakeLaunch(Step):
    """Stand-in for ``LaunchProvider`` — produces ``session`` and
    ``exit_code`` without hitting a real provider.
    """

    failure_policy = "halt"

    def __init__(self, session: Any = "S1", capture: list | None = None) -> None:
        self._session = session
        self._capture = capture

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="fake_launch",
            requires=frozenset({"interactive"}),
            produces=frozenset({"session", "exit_code"}),
        )

    def run(self, *, interactive: bool, **_: Any) -> dict[str, Any]:
        if self._capture is not None:
            self._capture.append("fake_launch")
        return {"session": self._session, "exit_code": 0}


def test_prestub_runs_with_empty_state() -> None:
    pipe = build(PreStub())
    state = run(pipe, {})
    assert state == {}


def test_poststub_default_is_no_op() -> None:
    pipe = build(PostStub())
    state = run(pipe, {})
    assert state == {}


def test_poststub_with_required_key_fails_without_upstream() -> None:
    pipe = build(PostStub(requires=frozenset({"session"})))
    with pytest.raises(BuildError, match="session"):
        run(pipe, {})


def test_poststub_receives_session_from_prior_step() -> None:
    capture: list = []

    class _SessionInspector(Step):
        failure_policy = "continue"

        @property
        def io(self) -> StepIO:
            return StepIO(
                name="inspector",
                requires=frozenset({"session"}),
            )

        def run(self, *, session: Any, **_: Any) -> dict[str, Any]:
            capture.append(session)
            return {}

    pipe = build(_FakeLaunch(session="SENTINEL"), _SessionInspector())
    run(pipe, {"interactive": True})
    assert capture == ["SENTINEL"]


def test_three_step_pipeline_runs_in_order() -> None:
    capture: list = []

    class _Tracer(PreStub):
        def __init__(self, label: str) -> None:
            self._label = label

        @property
        def io(self) -> StepIO:
            # Inherit PreStub's IO shape (no requires/produces) but with
            # a unique name so build allows two of them in the same pipe.
            return StepIO(name=f"tracer_{self._label}")

        def run(self, **inputs: Any) -> dict[str, Any]:
            capture.append(self._label)
            return {}

    fl = _FakeLaunch(capture=capture)
    pipe = build(_Tracer("pre"), fl, _Tracer("post"))
    run(pipe, {"interactive": False})
    assert capture == ["pre", "fake_launch", "post"]


def test_prestub_and_poststub_compose_around_fake_launch() -> None:
    """The actual Phase 1 preset shape: PreStub → launch → PostStub."""
    pipe = build(PreStub(), _FakeLaunch(), PostStub())
    state = run(pipe, {"interactive": False})
    assert state["session"] == "S1"
    assert state["exit_code"] == 0
