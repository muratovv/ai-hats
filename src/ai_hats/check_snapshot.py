"""What a legacy surface silently drops at launch (HATS-1207, HATS-1241).

HATS-1540 retired ``snapshot_checks`` and the ``<sid>/checks/`` root it wrote:
the SKILLS category already mirrors every composed skill unconditionally
(``SessionPolicy`` has no skills field), with the same lifetime and one writer,
so a second copy bought nothing and cost a session that predates a binding every
transition until restart. What survives here is the launch notice: a surface
below the artifact builder writes no mirror either, and that has to be said out
loud rather than discovered when a gate does not fire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats_core import CompositionResult

    from .session_artifacts import SessionPolicy


def legacy_launch_notices(
    provider_name: str,
    result: CompositionResult,
    policy: SessionPolicy,
) -> list[str]:
    """What a pre-ADR-0018 surface silently drops at launch.

    Such a provider is degraded to ``build_session_prompt``, which sits below
    both the policy and the skill mirror — so both losses are announced here.
    """
    from .session_artifacts import SessionPolicy as _Policy

    prefix = f"provider '{provider_name}' predates the artifact builder: "
    notices = []
    if policy != _Policy():
        notices.append(f"{prefix}session policy {policy} is NOT applied to it.")
    if result.checks:
        skills = ", ".join(sorted({check.skill for check in result.checks}))
        notices.append(
            f"{prefix}composed skills are NOT mirrored ({skills}) — "
            f"a gate bound to one of them has no session root to resolve from."
        )
    return notices


__all__ = ["legacy_launch_notices"]
