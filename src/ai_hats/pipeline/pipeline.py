"""Pipeline + build/run per ADR-0001 §2-§3.

A ``Pipeline`` is itself a ``Step`` (recursive composition). ``build`` is a
two-phase entry point: it constructs the pipeline structure and validates
self-consistency (every step's ``requires`` must be either in the
pipeline's external requires or produced by an earlier step). ``run``
executes steps sequentially, threading state via projection — each step
sees only the keys it declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .step import FailurePolicy, Step, StepError, StepIO


class BuildError(ValueError):
    """Build-time contract violation (e.g. undeclared requires)."""


@dataclass(frozen=True)
class Pipeline(Step):
    steps: tuple[Step, ...]
    pipeline_name: str = "pipeline"
    failure_policy: FailurePolicy = "halt"

    @property
    def io(self) -> StepIO:
        produced: set[str] = set()
        external_req: set[str] = set()
        external_opt: set[str] = set()
        for s in self.steps:
            external_req |= (s.io.requires - produced)
            external_opt |= (s.io.optional - produced - external_req)
            produced |= s.io.produces
        return StepIO(
            name=self.pipeline_name,
            requires=frozenset(external_req),
            optional=frozenset(external_opt),
            produces=frozenset(produced),
        )

    def run(self, **inputs: Any) -> dict[str, Any]:
        return _run_steps(
            self.steps, dict(inputs), parent_policy=self.failure_policy
        )


def build(*steps: Step, name: str = "pipeline") -> Pipeline:
    """Construct a Pipeline. Validation against actual inputs is in ``run``.

    Anything a step requires but no earlier step produces becomes part of
    the pipeline's external ``requires`` — the implicit set of keys that
    callers must supply via ``initial``. ``run`` is what checks those
    against the actual initial state.
    """
    return Pipeline(steps=tuple(steps), pipeline_name=name)


def run(
    pipeline: Pipeline, initial: Mapping[str, Any]
) -> dict[str, Any]:
    """Execute pipeline against ``initial`` state, threading projections."""
    available = set(initial.keys())
    produced: set[str] = set()
    for s in pipeline.steps:
        missing = s.io.requires - produced - available
        if missing:
            raise BuildError(
                f"{s.io.name}: undeclared requires {sorted(missing)} "
                f"(not in initial keys and not produced by prior steps)"
            )
        produced |= s.io.produces
    return _run_steps(
        pipeline.steps, dict(initial), parent_policy=pipeline.failure_policy
    )


def _run_steps(
    steps: tuple[Step, ...],
    state: dict[str, Any],
    *,
    parent_policy: FailurePolicy,
) -> dict[str, Any]:
    """Sequential execution with projection→step.run→delta-merge."""
    del parent_policy  # reserved for nested composites; not used in Phase 1
    for s in steps:
        kwargs = {k: state[k] for k in s.io.requires}
        kwargs.update({k: state[k] for k in s.io.optional if k in state})
        try:
            delta = s.run(**kwargs)
        except Exception as e:
            if s.failure_policy == "halt":
                raise
            state.setdefault("errors", {})[s.io.name] = e
            continue
        unexpected = set(delta.keys()) - s.io.produces
        if unexpected:
            raise StepError(
                f"{s.io.name}: emitted unexpected keys {sorted(unexpected)} "
                f"(declared produces: {sorted(s.io.produces)})"
            )
        state.update(delta)
    return state
