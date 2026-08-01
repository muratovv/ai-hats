"""Session check snapshot: the bytes a bound check runs from (HATS-1241).

ADR-0019 D-b. At session entry every ``CompositionResult.checks`` binding gets
the declaring skill's directory copied WHOLE into ``<sid>/checks/<skill>/`` —
whole, so sibling data files survive, and through the ``Materializer`` port, so
``--dry-run`` and the launch record see the same write the session performs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from pathlib import Path

    from ai_hats_core import CompositionResult

    from .materialization import Materializer
    from .session_artifacts import SessionPolicy


def legacy_launch_notices(
    provider_name: str,
    result: CompositionResult,
    policy: SessionPolicy,
) -> list[str]:
    """What a pre-ADR-0018 surface silently drops at launch (HATS-1207, HATS-1241).

    Such a provider is degraded to ``build_session_prompt``, which sits below
    both the policy and the snapshot — so both losses are announced here rather
    than discovered when a gate does not fire.
    """
    from .session_artifacts import SessionPolicy as _Policy

    prefix = f"provider '{provider_name}' predates the artifact builder: "
    notices = []
    if policy != _Policy():
        notices.append(f"{prefix}session policy {policy} is NOT applied to it.")
    if result.checks:
        skills = ", ".join(sorted({check.skill for check in result.checks}))
        notices.append(
            f"{prefix}bound checks are NOT snapshotted ({skills}) — "
            f"a gate bound to one of them has no session root to resolve from."
        )
    return notices


def snapshot_checks(
    project_dir: Path,
    result: CompositionResult,
    session_id: str,
    *,
    port: Materializer,
) -> None:
    """Copy every bound skill's directory into this session's checks root."""
    if not result.checks:
        return

    from .libraries.models import CheckBindingError, resolve_namespace
    from .paths import session_checks_dir

    root = session_checks_dir(project_dir, session_id)
    by_name = {resolve_namespace(skill.name): skill for skill in result.skills}
    for name in dict.fromkeys(resolve_namespace(check.skill) for check in result.checks):
        skill = by_name.get(name)
        if skill is None:
            raise CheckBindingError(
                f"checks: bound skill {name!r} is not in the composition — "
                f"it cannot be snapshotted, so its gate would be silently absent"
            )
        dest = (root / name).resolve()
        if not dest.is_relative_to(root.resolve()):
            raise CheckBindingError(
                f"checks: snapshot of skill {name!r} would land at {dest}, outside "
                f"this session's checks root {root} — refusing to write there"
            )
        if dest.exists():
            continue  # first writer wins: the session's bytes never change under it
        port.copy_tree(skill.source_path, dest)
