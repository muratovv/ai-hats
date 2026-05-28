"""Step contract per ADR-0001 §1.

A Step never sees global state — it gets only the keys declared in its
``StepIO`` and returns only a delta whose keys are a subset of ``produces``.
This is the foundation that makes pipelines composable, testable in
isolation, and validatable at build time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from .harness_policy import HarnessPolicy


FailurePolicy = Literal["halt", "continue"]


@dataclass(frozen=True)
class StepIO:
    name: str
    requires: frozenset[str] = field(default_factory=frozenset)
    optional: frozenset[str] = field(default_factory=frozenset)
    produces: frozenset[str] = field(default_factory=frozenset)


class StepError(RuntimeError):
    """Step contract violation at runtime (e.g. unexpected delta keys)."""


class Step(ABC):
    failure_policy: FailurePolicy = "halt"

    # HATS-378: opt-in harness reliability policy attached by the YAML
    # loader. Default None means no zero-output guard and no timeout
    # retry — current behaviour. Steps that spawn sub-agents read this
    # attribute and propagate it into their inner runner.
    harness_policy: HarnessPolicy | None = None

    # HATS-584: optional per-step wall-clock timeout in seconds. ``None``
    # (default) keeps current behaviour — no bound. When set, ``run`` bounds
    # the step in a worker thread and raises ``PipelineCancelled`` on the
    # deadline. This is the OUTER pipeline-level net; it is orthogonal to the
    # harness-level subprocess timeout (HATS-378), which stays authoritative
    # for sub-agent subprocesses.
    timeout: float | None = None

    @property
    @abstractmethod
    def io(self) -> StepIO: ...

    @abstractmethod
    def run(self, **inputs: Any) -> dict[str, Any]:
        """Returns a dict whose keys are a subset of ``self.io.produces``."""

    def on_cancel(self, **inputs: Any) -> dict[str, Any] | None:
        """HATS-584: cleanup hook the runner invokes on timeout/cancel.

        Default is a no-op. A step that owns a cancellable resource (e.g. a
        live subprocess) overrides this to release it — typically a
        process-group kill — and may return a partial-result delta whose keys
        are a subset of ``io.produces`` (merged via the None-filter funnel
        rule). It receives the same projected inputs ``run`` would have.

        MUST be safe to call concurrently with an in-flight ``run`` left
        running in the orphaned worker thread after a timeout: snapshot /
        release only, guarded by the step's own synchronization.
        """
        del inputs
        return None
