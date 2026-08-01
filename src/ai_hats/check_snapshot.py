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
        port.copy_tree(skill.source_path, root / name)
