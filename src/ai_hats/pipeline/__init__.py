"""Typed dataflow pipeline runtime per ADR-0001.

Public API:

- ``StepIO`` / ``Step`` — contract for pipeline-composable units of work.
- ``Pipeline`` — composite step that runs children sequentially with
  projection-based state threading.
- ``build`` / ``run`` — constructor and executor.
- ``StepError`` / ``BuildError`` — contract violations.
"""

from .pipeline import BuildError, Pipeline, build, run
from .step import FailurePolicy, Step, StepError, StepIO

__all__ = [
    "BuildError",
    "FailurePolicy",
    "Pipeline",
    "Step",
    "StepError",
    "StepIO",
    "build",
    "run",
]
