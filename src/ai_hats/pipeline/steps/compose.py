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
        # HATS-452 (П3 in ADR-0005): when no role is requested, OMIT the
        # ``system_prompt`` key entirely instead of emitting ``""``.
        # Downstream (``LaunchProvider`` → ``WrapRunner``) treats a
        # missing key and ``None`` as identical — both mean "no override,
        # let the runner compose the role from on-disk state". The
        # previous empty-string return tripped ``WrapRunner.run_session``'s
        # ``if system_prompt_override is not None`` guard, which then
        # replaced the freshly-composed 16k-character injection list with
        # ``[""]``. The pipeline funnel merge boundary also drops
        # ``None`` values (HATS-452 in pipeline.py), so producers that
        # prefer to emit ``{"system_prompt": None}`` get the same
        # behaviour.
        if not role:
            return {}
        from ...assembler import Assembler
        from ...materialize import compose_for_role

        # HATS-501: route through the ``compose_for_role`` facade so the
        # funnel value reflects the *layered* composition (built-in role
        # + global overlay + project overlay), matching every other
        # composition consumer (HATS-456). The previous direct
        # ``composer.compose(role)`` call skipped overlays, so global /
        # project ``injection_append`` and ``add_traits`` injection
        # bodies were dropped from the funnel — and then propagated
        # through ``LaunchProvider`` as ``system_prompt_override`` into
        # ``SubAgentRunner._run_attempt``'s
        # ``result.with_injection_override(...)``, replacing the
        # correctly-composed list wholesale. Sister contract:
        # ``test_funnel_value_contract.py``.
        asm = Assembler(project_dir)
        result = compose_for_role(asm, role)
        if result.errors:
            raise RuntimeError(
                f"compose_role: failed to resolve role {role!r}: {result.errors}"
            )
        return {"system_prompt": result.merged_injection}
