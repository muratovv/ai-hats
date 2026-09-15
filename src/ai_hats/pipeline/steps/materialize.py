"""``materialize_system_prompt`` step — project the seeded composition's plan.

"What would the agent actually see for role X" (ADR-0005 D1) is the plan's
``prompt.text`` — a record, not a second render (ADR-0036 D5). The step neither
composes nor renders: the integrator adapts the composition at the compose
seam (``composition_seam.build_preview_payload``, which owns the no-role /
no-provider / compose-errors validation) and seeds it as ``composition``.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..step import Step, StepIO


class MaterializeSystemPrompt(Step):
    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params  # no params for this step

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="materialize_system_prompt",
            requires=frozenset({"composition"}),
            produces=frozenset({"system_prompt_text", "composition_stats"}),
        )

    def run(self, *, composition: Any, **_: Any) -> dict[str, Any]:
        result = composition.result
        if composition.plan is None:
            raise RuntimeError(
                "materialize_system_prompt: the payload carries no adapted plan; "
                "compose it at the seam (build_preview_payload)"
            )
        prompt_text = composition.plan.prompt.text
        return {
            "system_prompt_text": prompt_text,
            "composition_stats": {
                "role": composition.effective_role,
                "provider": composition.provider.name,
                "trait_count": len(result.trait_injections),
                "trait_names": list(result.trait_injections.keys()),
                "rule_count": len(result.rules),
                "skill_count": len(result.skills),
                "injection_chars": len(result.merged_injection),
                "prompt_chars": len(prompt_text),
            },
        }
