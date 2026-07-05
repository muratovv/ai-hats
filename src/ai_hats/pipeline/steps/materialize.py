"""``materialize_system_prompt`` step — render the seeded composition.

The single source of truth for "what would the agent actually see for role X
under provider Y" (HATS-452 / ADR-0005 П1). HATS-865: the step no longer
composes — the integrator builds a preview payload at the compose seam
(``composition_seam.build_preview_payload``, which owns the no-role /
no-provider / compose-errors validation) and seeds it as ``composition``;
this step renders it through the payload's provider and emits the stats.
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
        prompt_text = composition.provider.build_system_prompt(result)
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
