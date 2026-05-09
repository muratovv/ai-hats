"""``resolve_prompt`` step — read a harness-prepared prompt file.

Per ADR-0002 §Q4 the pipeline only sees ``prompt_path: Path``. Any
short-name / raw-text / glue-with-handoff resolution lives in the
harness wrapper (``cli.execute._resolve_prompt`` style) before pipeline
``run`` is invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class ResolvePrompt(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        params = params or {}
        self.default_text: str = params.get("default_text", "")

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="resolve_prompt",
            optional=frozenset({"prompt_path"}),
            produces=frozenset({"prompt_text"}),
        )

    def run(
        self, *, prompt_path: Path | None = None, **_: Any,
    ) -> dict[str, Any]:
        if prompt_path is not None:
            return {"prompt_text": Path(prompt_path).read_text()}
        return {"prompt_text": self.default_text}
