"""Typed dataflow pipeline runtime per ADR-0001; the area's only entrance (ADR-0026 D14).

Three audiences: callers run a configured pipeline (``run_pipeline``, or
``run_subpipeline`` inside a session that already exists, plus ``PipelineConfig`` and
the ``RunParams`` / ``SubpipelineParams`` protocols their own params object implements,
and ``warm`` for one they will run later); step authors compose one (``Step`` /
``StepIO`` / ``Pipeline`` / ``build`` / ``run`` / ``CancelToken``); whoever runs a step
marked ``harness.reporting`` reads the ``HarnessPolicy`` this area parsed out of its
YAML. Everything else here is internal — the funnel vocabulary and the harness included.
"""

from .cancel import CancelReason, CancelToken
from .contract import (
    PipelineConfig,
    PipelineResult,
    PromptWriter,
    RunParams,
    SessionRef,
    SubpipelineParams,
    run_pipeline,
    run_subpipeline,
    warm,
)
from .harness_policy import HarnessPolicy
from .pipeline import BuildError, Pipeline, PipelineCancelled, build, run
from .step import FailurePolicy, Step, StepError, StepIO

__all__ = [
    "BuildError",
    "CancelReason",
    "CancelToken",
    "FailurePolicy",
    "HarnessPolicy",
    "Pipeline",
    "PipelineCancelled",
    "PipelineConfig",
    "PipelineResult",
    "PromptWriter",
    "RunParams",
    "SessionRef",
    "Step",
    "StepError",
    "StepIO",
    "SubpipelineParams",
    "build",
    "run",
    "run_pipeline",
    "run_subpipeline",
    "warm",
]
