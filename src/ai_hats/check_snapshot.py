"""What a launch says about the gates it arms (HATS-1207, HATS-1241, HATS-1548).

Two jobs, one subject. :func:`describe_checks` resolves every binding the way
the session will and reports whether the launch writes the bytes it will run —
the only observable there is, since HATS-1540 retired ``snapshot_checks`` and
the ``<sid>/checks/`` root: the skill mirror is written per SKILL, so no part of
the materialization plan depends on a binding existing. The notices below cover
the surfaces that cannot root one at all, said at launch rather than discovered
when a gate does not fire.

The module keeps its name for now; the snapshot it was named for is gone.
"""  # comment-length: allow — the retired subject must say what replaced it

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from pathlib import Path

    from ai_hats_core import CompositionResult, ResolvedCheck

    from .materialization import MaterializationPlan
    from .session_artifacts import SessionPolicy


@dataclass(frozen=True)
class ReportedCheck:
    """One binding as the launch report sees it (HATS-1548).

    ``runs_from`` is ``None`` when the binding cannot be resolved at all; the
    reason then rides ``SessionReport.notes`` rather than raising, because a
    report that dies on a broken gate tells the operator less than one that
    names it.
    """

    binding: ResolvedCheck
    runs_from: Path | None
    planned: bool


def describe_checks(
    provider,
    project_dir: Path,
    result: CompositionResult,
    session_id: str,
    plan: MaterializationPlan,
) -> tuple[tuple[ReportedCheck, ...], tuple[str, ...]]:
    """Resolve every binding the way the session will, and cross-check the plan.

    The binding list alone is the weak half — it is already readable in the role.
    What no other surface answers is whether the resolution settles here: same
    mirror the plan writes, same leaf spelling. Takes the provider the caller
    already holds: a report about THIS launch must not consult a second surface
    lookup that could answer differently.
    """
    from .check_resolve import (
        CheckResolutionError,
        mirror_for,
        rebase_onto_mirror,
        reject_worktree_root,
    )

    if not result.checks:
        return (), ()

    root = provider.session_skills_root(project_dir, session_id)
    if root is None:
        # Every binding is unresolvable for the same reason, and
        # `surface_skew_notice` already says it once — do not repeat it per row.
        return tuple(ReportedCheck(c, None, False) for c in result.checks), ()

    mirror = mirror_for(root, result)
    reported: list[ReportedCheck] = []
    notes: list[str] = []
    for binding in result.checks:
        try:
            # Same order the session uses: the worktree clause guards the path
            # the COMPOSITION resolved, before a root is picked (ADR-0019 D9).
            reject_worktree_root(binding.script_path, binding)
            runs_from = rebase_onto_mirror(binding, mirror).script_path
        except CheckResolutionError as exc:
            reported.append(ReportedCheck(binding, None, False))
            notes.append(str(exc))
            continue
        reported.append(ReportedCheck(binding, runs_from, _plan_covers(plan, runs_from)))
    return tuple(reported), tuple(notes)


def _plan_covers(plan: MaterializationPlan, runs_from: Path) -> bool:
    """Whether this launch writes the tree the script will be read out of.

    Both sides are resolved before comparing: ``runs_from`` comes back from
    ``rebase_onto_mirror`` already resolved, while a plan entry carries the path
    as the writer spelled it. On macOS a cache root under ``/tmp`` is a symlink
    to ``/private/tmp``, so the unresolved parent never matched the resolved
    child and every armed gate was reported "NOT written by this launch" — a
    false alarm in the one report that exists to tell the operator otherwise.
    """  # comment-length: allow — the symlink asymmetry is the whole bug
    from .materialization import WriteKind

    target = runs_from.resolve()
    return any(
        entry.kind is WriteKind.COPY_TREE and entry.target.resolve() in target.parents
        for entry in plan.entries
    )


def surface_skew_notice(provider_name: str, provider, project_dir, result) -> str | None:
    """Said at LAUNCH when this surface cannot root a bound check (HATS-1540).

    ``Surface.session_skills_root`` is concrete and defaults to ``None``, so a
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
