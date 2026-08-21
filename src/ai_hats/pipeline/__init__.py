"""Typed dataflow pipeline runtime per ADR-0001; the area's only entrance (ADR-0026 D14).

Two audiences: callers run a configured pipeline (``run_pipeline`` and the params /
result types), step authors compose one (``Step`` / ``StepIO`` / ``Pipeline`` /
``build`` / ``run`` / ``CancelToken``). Everything else here is internal — the funnel
vocabulary and the harness included.
"""

from .cancel import CancelReason, CancelToken
from .contract import (
    Automate,
    HarnessParams,
    Hitl,
    PipelineConfig,
    PipelineResult,
    RoleParams,
    RunParams,
    SessionParams,
    SessionRef,
    run_pipeline,
)
from .pipeline import BuildError, Pipeline, PipelineCancelled, build, run
from .step import FailurePolicy, Step, StepError, StepIO

__all__ = [
    "Automate",
    "BuildError",
    "CancelReason",
    "CancelToken",
    "FailurePolicy",
    "HarnessParams",
    "Hitl",
    "Pipeline",
    "PipelineCancelled",
    "PipelineConfig",
    "PipelineResult",
    "RoleParams",
    "RunParams",
    "SessionParams",
    "SessionRef",
    "Step",
    "StepError",
    "StepIO",
    "build",
    "run",
    "run_pipeline",
]
