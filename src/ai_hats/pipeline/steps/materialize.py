"""``materialize_system_prompt`` step — compose role + render through provider.

Produces the final system-prompt text that an interactive session would
inject via ``--system-prompt-file``, plus a small structured stats
record. This is the single source of truth for the question "what would
the agent actually see for role X under provider Y" — the canonical
materialization surface (HATS-452 / ADR-0005 П1 follow-up).

HATS-456 (Phase 2 closure). Routes through the
``ai_hats.materialize.compose_for_role`` facade so this step, runtime
consumers (``WrapRunner``, ``SubAgentRunner``), and the on-disk
``Assembler.set_role`` writer all derive the composition through a
single function — no four-way drift possible. The step's own job
(text + stats) keeps the build step inline because it needs the
intermediate ``CompositionResult`` for the stats payload.
"""

from __future__ import annotations

from pathlib import Path
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
            requires=frozenset({"project_dir"}),
            optional=frozenset({"role", "provider"}),
            produces=frozenset({"system_prompt_text", "composition_stats"}),
        )

    def run(
        self,
        *,
        project_dir: Path,
        role: str | None = None,
        provider: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ...assembler import Assembler
        from ...materialize import compose_for_role
        from ...providers import get_provider

        asm = Assembler(project_dir)
        cfg = asm.project_config
        # Fallback chain mirrors WrapRunner.run_session for parity:
        # explicit override → active_role → default_role.
        eff_role = role or cfg.active_role or cfg.default_role
        if not eff_role:
            raise RuntimeError(
                "materialize_system_prompt: no role to materialize "
                "(no --role override, no active_role/default_role in "
                "ai-hats.yaml). Set one or pass `role=...` to the step."
            )
        eff_provider = provider or cfg.provider
        if not eff_provider:
            raise RuntimeError(
                "materialize_system_prompt: no provider configured. "
                "Set `provider:` in ai-hats.yaml or pass `provider=...`."
            )

        # HATS-456: single derivation point for "compose for role X".
        # Build step stays inline because stats need the intermediate
        # CompositionResult (the facade's materialize_system_prompt
        # discards it).
        result = compose_for_role(asm, eff_role)
        if result.errors:
            raise RuntimeError(
                f"materialize_system_prompt: compose errors for role "
                f"{eff_role!r}: {result.errors}"
            )

        prompt_text = get_provider(eff_provider).build_system_prompt(result)

        return {
            "system_prompt_text": prompt_text,
            "composition_stats": {
                "role": eff_role,
                "provider": eff_provider,
                "trait_count": len(result.trait_injections),
                "trait_names": list(result.trait_injections.keys()),
                "rule_count": len(result.rules),
                "skill_count": len(result.skills),
                "injection_chars": len(result.merged_injection),
                "prompt_chars": len(prompt_text),
            },
        }
