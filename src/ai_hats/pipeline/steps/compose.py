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


class RoleNotFoundError(Exception):
    """Raised by ``ComposeRole`` when the requested role is unknown.

    Carries the requested name plus the sorted list of available role
    names so the CLI handler can render a friendly error without
    re-querying the resolver. Defined in this module so the pipeline
    step has no ``click`` dependency.
    """

    def __init__(self, role: str, available: list[str]) -> None:
        self.role = role
        self.available = available
        super().__init__(f"Role {role!r} not found")


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

        # HATS-501 / HATS-505: route through the ``compose_for_role`` facade so
        # the funnel value reflects the *layered* composition (built-in role +
        # global + project overlay), matching every other consumer (HATS-456).
        # This funnel value is observability-only (``PreLog``); ``LaunchProvider``
        # does NOT feed it into runners — HITL (``WrapRunner``) and Automate
        # (``SubAgentRunner``) each compose via the facade themselves. Drift
        # guards: ``test_no_direct_compose_outside_facade`` +
        # ``test_no_direct_compose_inside_pipeline_subtree`` +
        # ``test_funnel_value_contract`` + the
        # ``test_compose_overlay_propagation`` regression catcher (HATS-505).
        asm = Assembler(project_dir)
        # Pre-check role existence so we can raise a typed exception the
        # CLI converts into a friendly "Available roles:" message. Cheap
        # — just enumerates role dirs across library_paths.
        from ...models import ComponentType

        available = asm.resolver.list_components(ComponentType.ROLE)
        if role not in available:
            raise RoleNotFoundError(role, available)
        result = compose_for_role(asm, role)
        if result.errors:
            raise RuntimeError(
                f"compose_role: failed to resolve role {role!r}: {result.errors}"
            )
        return {"system_prompt": result.merged_injection}
