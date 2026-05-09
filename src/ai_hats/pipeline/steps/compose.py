"""``compose_role`` step — resolves a role into a flat system prompt.

YAGNI per ADR-0002 §Q1: we expose only the merged injection string.
Structural data (rules/skills/traits) stays inside ``Composer`` and the
runner. If a future step ever needs it, add a parallel ``composition``
key without breaking the contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class ComposeRole(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params  # no params for this step

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="compose_role",
            requires=frozenset({"project_dir"}),
            optional=frozenset({"role"}),
            produces=frozenset({"system_prompt"}),
        )

    def run(
        self, *, project_dir: Path, role: str | None = None, **_: Any,
    ) -> dict[str, Any]:
        # Mirror WrapRunner's tolerance: a session with no role uses the
        # on-disk assembled state (CLAUDE.md etc.) — nothing to compose
        # in that case. Empty system_prompt downstream means the runner
        # falls through to the no-override path.
        if not role:
            return {"system_prompt": ""}
        from ...assembler import Assembler

        composer = Assembler(project_dir).composer
        result = composer.compose(role)
        if result.errors:
            raise RuntimeError(
                f"compose_role: failed to resolve role {role!r}: {result.errors}"
            )
        return {"system_prompt": result.merged_injection}
