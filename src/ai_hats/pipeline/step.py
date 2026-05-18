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

    @property
    @abstractmethod
    def io(self) -> StepIO: ...

    @abstractmethod
    def run(self, **inputs: Any) -> dict[str, Any]:
        """Returns a dict whose keys are a subset of ``self.io.produces``."""
