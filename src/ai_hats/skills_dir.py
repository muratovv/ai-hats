"""Ref-counted materializer for directory-convention skill registries.

Extracted from ClineProvider (HATS-963/981) for providers whose harness
discovers skills from a project-scoped dir (``.agy/skills/``,
``.cline/skills/``): the union of all live sessions' skills stays on disk;
a JSON marker keyed by session_id prevents parallel sessions from sweeping
each other's skills.  # HATS-993
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path


logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ai_hats_core import ResolvedComponent

MANAGED_MARKER = ".ai-hats-managed"
_LOCK_TIMEOUT = 30.0


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
            dirs_to_check.extend([session_skills_dir / skill.name / sub for sub in ("scripts", "bin")])
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
            dirs_to_check.extend([session_skills_dir / skill.name / sub for sub in ("scripts", "bin")])
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
    session_id: str,
    *,
    gitignore_entry: str | None = None,
) -> None:
    """Copy ``skills`` into ``skills_dir`` under a filelock; sweep orphans."""
    import filelock

    if gitignore_entry:
        _ensure_gitignored(project_dir, gitignore_entry)

    skills_dir.mkdir(parents=True, exist_ok=True)

    lock_path = skills_dir.parent / "skills.lock"
    lock = filelock.FileLock(str(lock_path), timeout=_LOCK_TIMEOUT)
    try:
        with lock:
            _rebuild(skills_dir, list(skills), project_dir, session_id)
    except filelock.Timeout as exc:
        raise RuntimeError(
            f"skills materialization blocked >{_LOCK_TIMEOUT:.0f}s on lock "
            f"{lock_path} — a stuck ai-hats process likely holds it. "
            f"If safe, remove the lock file and retry."
        ) from exc


def _rebuild(
    skills_dir: Path,
    skills: list[ResolvedComponent],
    project_dir: Path,
    session_id: str,
) -> None:
    """Additive ref-counted rebuild; caller holds the lock (HATS-981 pattern)."""
    from .placeholders import expand_fsm_edges_token, expand_path_placeholders

    marker = skills_dir / MANAGED_MARKER
    refs: dict[str, list[str]] = {}
    if marker.is_file():
        try:
            refs = json.loads(marker.read_text())
        except (json.JSONDecodeError, ValueError):
            refs = {}  # corrupt marker — start fresh

    prev_all = {name for names in refs.values() for name in names}

    desired = {s.name for s in skills if s.source_path.is_dir()}
    refs[session_id] = sorted(desired)

    new_all = {name for names in refs.values() for name in names}

    # Sweep skills that were managed but no session references anymore.
    for name in prev_all - new_all:
        stale = skills_dir / name
        if stale.is_dir():
            shutil.rmtree(stale)  # safe-delete: ok managed-skills-mirror sweep

    for skill in skills:
        if not skill.source_path.is_dir():
            continue
        dest = skills_dir / skill.name
        if dest.exists():
            shutil.rmtree(dest)  # safe-delete: ok managed-skills-mirror refresh
        shutil.copytree(skill.source_path, dest)
        # Expand <ai_hats_dir> (HATS-380) + inject the FSM edge table for the
        # {{backlog_fsm_edges}} token (HATS-1051); other assets verbatim.
        skill_md = dest / "SKILL.md"
        if skill_md.exists():
            original = skill_md.read_text()
            rendered = expand_fsm_edges_token(
                expand_path_placeholders(original, project_dir)
            )
            if rendered != original:
                skill_md.write_text(rendered)

    marker.write_text(json.dumps(refs, indent=2, sort_keys=True) + "\n")


def _ensure_gitignored(project_dir: Path, entry: str) -> None:
    """Idempotent: append ``entry`` to the project .gitignore if absent."""
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        lines = gitignore.read_text().splitlines()
        if entry in lines:
            return
        gitignore.write_text(gitignore.read_text().rstrip("\n") + f"\n{entry}\n")
    else:
        gitignore.write_text(f"{entry}\n")
