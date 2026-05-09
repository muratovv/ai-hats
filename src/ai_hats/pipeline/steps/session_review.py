"""``run_session_review`` step — atomic blackbox over SessionReviewRunner.

Phase-3 epic work will decompose this into 5+ post-steps
(compute_facts, build_prompt, spawn, extract, validate, save). For now
it stays atomic because the runner already encapsulates retries and
internal state we don't want to thread through pipeline state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class RunSessionReview(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        params = params or {}
        self.max_retries: int = int(params.get("max_retries", 1))

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="run_session_review",
            requires=frozenset({"session_id", "project_dir"}),
            optional=frozenset({"max_retries"}),
            produces=frozenset({"review_path"}),
        )

    def run(
        self,
        *,
        session_id: str,
        project_dir: Path,
        max_retries: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ...retro.session_review_runner import SessionReviewRunner

        # State override > YAML param default. Lets harness propagate
        # CLI flags (--max-retries) without YAML-level reconfiguration.
        retries = max_retries if max_retries is not None else self.max_retries
        runner = SessionReviewRunner(project_dir)
        review_path = runner.run(session_id, max_retries=retries)
        return {"review_path": review_path}
