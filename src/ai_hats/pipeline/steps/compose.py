"""``compose_role`` step — projects the seeded composition into the funnel.

HATS-865: the step no longer composes. The integrator caller composes ONCE at
the compose seam (``ai_hats.composition_seam.build_composition_payload``) and
seeds the :class:`~ai_hats.composition_payload.CompositionPayload` into the
pipeline initial state under the ``composition`` key; this step is a pure
projection of ``result.merged_injection`` for observability (``PreLog``).
Role-existence validation (``RoleNotFoundError``) lives at the seam now.
"""

from __future__ import annotations

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
            requires=frozenset(),
            optional=frozenset({"composition"}),
            produces=frozenset({"system_prompt"}),
        )

    def run(self, *, composition: Any = None, **_: Any) -> dict[str, Any]:
        # HATS-452 (П3): an absent/empty composition OMITS the key (never "").
        if composition is None:
            return {}
        return {"system_prompt": composition.result.merged_injection or None}
