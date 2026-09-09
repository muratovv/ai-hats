"""Materializer for directory-convention skill registries.

Extracted from ClineSurface (HATS-963/981) for providers whose harness
discovers skills from a directory convention (agy's ``rules/.agents/skills/``).
That dir was project-scoped when this module was written; since HATS-1166 it is
session-scoped, so the rebuild is a plain wipe-and-copy — see
:func:`materialize_skills_dir`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ai_hats_core import ResolvedComponent

    from .materialization import Materializer


logger = logging.getLogger(__name__)


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


def collect_skill_script_paths(
    skills: Iterable[ResolvedComponent],
    session_skills_dir: Path | None = None,
) -> list[Path]:
    """Collect existing `scripts/` and `bin/` directory paths from composed skills.

    If multiple skills declare scripts with identical filenames, PATH resolution
    follows composition order: the skill appearing earlier in ``skills`` takes
    precedence. Collisions are logged via logger.warning.
    """
    paths: list[Path] = []
    collisions = find_skill_script_collisions(skills, session_skills_dir)
    for msg in collisions:
        logger.warning(msg)

    for skill in skills:
        dirs_to_check: list[Path] = []
        if session_skills_dir is not None:
            dirs_to_check.extend(
                [session_skills_dir / skill.name / sub for sub in ("scripts", "bin")]
            )
        if hasattr(skill, "source_path") and skill.source_path and skill.source_path.is_dir():
            dirs_to_check.extend([skill.source_path / sub for sub in ("scripts", "bin")])

        for p in dirs_to_check:
            if p.is_dir() and p not in paths:
                paths.append(p)
    return paths


def inject_skill_paths_to_env(
    env: dict[str, str],
    skills: Iterable[ResolvedComponent],
    session_skills_dir: Path | None = None,
) -> None:
    """Prepend skill `scripts/` and `bin/` directories to env["PATH"] in place."""
    script_paths = collect_skill_script_paths(skills, session_skills_dir)
    if not script_paths:
        return
    current_path = env.get("PATH") or os.environ.get("PATH", "")
    existing_parts = current_path.split(":") if current_path else []

    new_parts = [str(p) for p in script_paths if str(p) not in existing_parts]
    if not new_parts:
        return

    env["PATH"] = ":".join(new_parts + existing_parts)


def materialize_skills_dir(
    skills_dir: Path,
    skills: Iterable[ResolvedComponent],
    project_dir: Path,
    port: "Materializer",
) -> None:
    """Wipe ``skills_dir`` and copy ``skills`` in.

    HATS-1248: this used to be a ref-counted rebuild behind a filelock — a JSON
    marker keyed by session_id, so parallel sessions would not sweep each other's
    skills. But the target is itself keyed by session_id, so the map could only
    hold a second entry when two processes minted the SAME id, and then they
    shared one ref slot and overwrote each other anyway. With ids unique the
    dir has exactly one writer, and this is a plain wipe-and-copy (what cline
    has always done for its session-scoped equivalent).
    """
    from .placeholders import expand_fsm_edges_token, expand_path_placeholders

    port.remove_tree(skills_dir)
    port.mkdir(skills_dir)

    for skill in skills:
        if not skill.source_path.is_dir():
            continue
        dest = skills_dir / skill.name
        port.copy_tree(skill.source_path, dest)
        # Expand <ai_hats_dir> + inject the FSM edge table.
        # Read the SOURCE: under a PlanMaterializer the copy does not exist.
        source_md = skill.source_path / "SKILL.md"
        if source_md.exists():
            original = source_md.read_text()
            rendered = expand_fsm_edges_token(
                expand_path_placeholders(original, project_dir), project_dir
            )
            if rendered != original:
                port.write_text(dest / "SKILL.md", rendered)
