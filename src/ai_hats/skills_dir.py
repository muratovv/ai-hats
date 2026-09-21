"""What the composed skills' script directories collide on.

The mirror itself is planned (``surfaces.mirror``); this is the one read of
the composed skills that stays with the adapter: two skills shipping a script
of one name, where PATH order decides which the agent gets.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ai_hats_core import ResolvedComponent


def find_skill_script_collisions(
    skills: Iterable[ResolvedComponent],
    session_skills_dir: Path | None = None,
) -> list[str]:
    """Find script filename collisions across composed skills.

    Returns a list of human-readable warning strings suitable for startup notices.
    """
    seen_scripts: dict[str, str] = {}  # filename -> skill_name
    collisions: list[str] = []

    for skill in skills:
        dirs_to_check: list[Path] = []
        if session_skills_dir is not None:
            dirs_to_check.extend(
                [session_skills_dir / skill.name / sub for sub in ("scripts", "bin")]
            )
        if hasattr(skill, "source_path") and skill.source_path and skill.source_path.is_dir():
            dirs_to_check.extend([skill.source_path / sub for sub in ("scripts", "bin")])

        for p in dirs_to_check:
            if p.is_dir():
                for item in p.iterdir():
                    if item.is_file() and not item.name.startswith("."):
                        if item.name in seen_scripts and seen_scripts[item.name] != skill.name:
                            msg = (
                                f"Script name collision: {item.name!r} in skill {skill.name!r} "
                                f"is shadowed by skill {seen_scripts[item.name]!r} earlier in PATH"
                            )
                            if msg not in collisions:
                                collisions.append(msg)
                        else:
                            seen_scripts[item.name] = skill.name
    return collisions
