"""Typed dataflow pipeline runtime per ADR-0001; the area's only entrance (ADR-0026 D14).

Two audiences: callers launch a pipeline (``launch``, ``PipelineId``, the request and
outcome types), step authors compose one (``Step`` / ``StepIO`` / ``Pipeline`` /
``build`` / ``run`` / ``CancelToken``). Everything else here is internal — the funnel
vocabulary and the harness included.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .cancel import CancelReason, CancelToken
from .contract import (
    PipelineId,
    PipelineOutcome,
    PipelineSession,
    RoleSessionRequest,
)
from .pipeline import BuildError, Pipeline, PipelineCancelled, build, run
from .step import FailurePolicy, Step, StepError, StepIO


@contextmanager
def launch(
    pipeline_id: PipelineId,
    project_dir: Path,
    *,
    session_id: str | None = None,
) -> Iterator[PipelineSession]:
    """Run one core pipeline: per-session namespace, GC, trace wiring, user steps."""
    from .harness import PipelineHarness  # deferred: costs ~160 ms of import at startup

    with PipelineHarness(pipeline_id.value, project_dir, session_id=session_id) as harness:
        yield PipelineSession(harness)


__all__ = [
    "BuildError",
    "CancelReason",
    "CancelToken",
    "FailurePolicy",
    "Pipeline",
    "PipelineCancelled",
    "PipelineId",
    "PipelineOutcome",
    "PipelineSession",
    "RoleSessionRequest",
    "Step",
    "StepError",
    "StepIO",
    "build",
    "launch",
    "run",
]
