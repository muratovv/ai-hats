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


def surface_skew_notice(provider_name: str, provider, project_dir, result) -> str | None:
    """Said at LAUNCH when this surface cannot root a bound check (HATS-1540).

    ``Provider.session_skills_root`` is concrete and defaults to ``None``, so a
    surface package older than the accessor keeps importing — and then every
    bound transition in its sessions is refused, with a message about a missing
    file. That is the HATS-1538 brick shape from a new cause, and the operator
    should learn it when the session starts rather than at the first gate.

    ``legacy_launch_notices`` below does NOT cover this: it fires only for a
    surface below the artifact builder, and an out-of-date agy/cline implements
    that seam perfectly well. Two different skews, two notices.
    """  # comment-length: allow — the ADR claimed this was covered; it was not
    if not result.checks:
        return None
    if provider.session_skills_root(project_dir, "probe") is not None:
        return None
    skills = ", ".join(sorted({check.skill for check in result.checks}))
    return (
        f"provider {provider_name!r} does not say where it mirrors a session's skills, so the "
        f"bound check(s) from {skills} have no bytes to run: EVERY transition and every "
        f"`ai-hats wt merge` in this session will be refused. Upgrade the surface package "
        f"(it needs `session_skills_root`, added in HATS-1540) or unbind the check."
    )


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


__all__ = ["legacy_launch_notices", "surface_skew_notice"]
