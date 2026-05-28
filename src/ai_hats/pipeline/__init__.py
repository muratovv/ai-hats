"""Typed dataflow pipeline runtime per ADR-0001.

Public API:

- ``StepIO`` / ``Step`` — contract for pipeline-composable units of work.
- ``Pipeline`` — composite step that runs children sequentially with
  projection-based state threading.
- ``build`` / ``run`` — constructor and executor.
- ``StepError`` / ``BuildError`` — contract violations.
- ``CancelToken`` / ``CancelReason`` — cooperative cancellation primitive
  (HATS-584); threaded by ``run`` for per-step timeout / external cancel.
"""

from .cancel import CancelReason, CancelToken
from .pipeline import BuildError, Pipeline, PipelineCancelled, build, run
from .step import FailurePolicy, Step, StepError, StepIO

__all__ = [
    "BuildError",
    "CancelReason",
    "CancelToken",
    "FailurePolicy",
    "Pipeline",
    "PipelineCancelled",
    "Step",
    "StepError",
    "StepIO",
    "build",
    "run",
]
