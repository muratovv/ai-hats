"""``spawn_session_review`` step — fire-and-forget session-reviewer launch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class SpawnSessionReview(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        params = params or {}
        self.max_retries: int = int(params.get("max_retries", 1))

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="spawn_session_review",
            requires=frozenset({"session_id", "project_dir"}),
            produces=frozenset({"review_pid"}),
        )

    def run(
        self, *, session_id: str, project_dir: Path, **_: Any,
    ) -> dict[str, Any]:
        from ...paths import RETRO_LOG, runs_dir, session_dirname

        log_path = runs_dir(project_dir) / session_dirname(session_id) / RETRO_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m", "ai_hats.cli.reflect_session_main",
                    session_id,
                    str(self.max_retries),
                ],
                cwd=str(project_dir),
                stdout=f,
                stderr=f,
                start_new_session=True,
            )
        return {"review_pid": proc.pid}
