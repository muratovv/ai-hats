"""Every step's ``run`` must be callable from its own declaration (HATS-1892).

``_run_steps`` builds kwargs strictly from the declaration, so a required ``run``
param it omits can never be passed: under ``failure_policy="continue"`` the step
raises ``TypeError`` and is skipped for its whole life while the pipeline stays
green — how ``quorum_autoclose`` sat dead in ``finalize-hitl``.

Two nets over one implementation: unit tests of the guard, and a sweep feeding it
every registered step — the advertised ids no shipped pipeline wires included.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_hats.pipeline import registry
from ai_hats.pipeline.pipeline import BuildError, _required_run_params, build
from ai_hats.pipeline.step import Step, StepIO


#: Steps whose ``__init__`` refuses an empty mapping — minimal params to construct one.
_CONSTRUCTOR_PARAMS: dict[str, dict[str, Any]] = {
    "emit_stdout": {"key": "x"},
    "extract_marker": {"start": "<a>", "end": "</a>", "out_key": "x"},
    "save_artifact": {"key": "x", "out_path_template": "out.txt"},
}


class _Declared(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="declared", requires=frozenset({"alpha"}))

    def run(self, *, alpha: str, **_: Any) -> dict[str, Any]:
        del alpha
        return {}


class _Undeclared(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="undeclared", requires=frozenset({"alpha"}))

    def run(self, *, beta: str, **_: Any) -> dict[str, Any]:
        del beta
        return {}


class _RequiredUnderOptional(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="required_under_optional", optional=frozenset({"beta"}))

    def run(self, *, beta: str, **_: Any) -> dict[str, Any]:
        del beta
        return {}


class _KwargsOnly(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="kwargs_only", requires=frozenset({"alpha"}))

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs
        return {}


class _Defaulted(Step):
    @property
    def io(self) -> StepIO:
        return StepIO(name="defaulted", optional=frozenset({"beta"}))

    def run(self, *, beta: str | None = None, **_: Any) -> dict[str, Any]:
        del beta
        return {}


def test_run_params_excludes_self_and_defaults():
    assert _required_run_params(_Declared()) == {"alpha"}
    assert _required_run_params(_Defaulted()) == frozenset()
    assert _required_run_params(_KwargsOnly()) == frozenset()


def test_build_accepts_a_declared_param():
    step = _Declared()
    assert build(step).steps == (step,)


def test_build_refuses_an_undeclared_required_param():
    with pytest.raises(BuildError) as exc:
        build(_Undeclared())
    assert "undeclared" in str(exc.value)
    assert "'beta'" in str(exc.value)


def test_build_refuses_a_required_param_declared_optional():
    # `optional` permits the key to be absent, so the call is a latent TypeError.
    with pytest.raises(BuildError) as exc:
        build(_RequiredUnderOptional())
    assert "optional" in str(exc.value)


def test_build_accepts_kwargs_only_and_defaulted_runs():
    build(_KwargsOnly(), _Defaulted())


@pytest.mark.parametrize("step_id", registry.names())
def test_every_registered_step_can_be_called_from_its_declaration(step_id: str):
    step = registry.get(step_id)(_CONSTRUCTOR_PARAMS.get(step_id, {}))
    undeclared = _required_run_params(step) - step.io.requires
    assert not undeclared, (
        f"{step_id}: run() requires {sorted(undeclared)}, absent from "
        f"io.requires {sorted(step.io.requires)} — the runner can never pass them"
    )
