"""``build_handoff`` step — render HYP+PROP handoff for reflect-all."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from typing import Any, Mapping

from ..step import Step, StepIO


class BuildHandoff(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="build_handoff",
            requires=frozenset({"layout"}),
            produces=frozenset({"handoff_path"}),
        )

    def run(self, *, layout: ProjectLayout, **_: Any) -> dict[str, Any]:
        project_dir = layout.root
        from ...cli.reflect import _build_handoff

        return {"handoff_path": _build_handoff(project_dir)}
