"""``save_artifact`` step — write a state value to a templated path.

MVP supports only the ``{ts}`` placeholder in the path template. If a
future pipeline needs more (e.g. ``{session_id}``) extend to
``template.format(**state)`` then.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class SaveArtifact(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any]) -> None:
        for required in ("key", "out_path_template"):
            if required not in params:
                raise ValueError(f"save_artifact: missing param {required!r}")
        self.key: str = params["key"]
        self.out_path_template: str = params["out_path_template"]

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="save_artifact",
            requires=frozenset({self.key}),
            produces=frozenset({"saved_path"}),
        )

    def run(self, **inputs: Any) -> dict[str, Any]:
        content = inputs[self.key]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        path = Path(self.out_path_template.format(ts=ts))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else str(content))
        return {"saved_path": path}
