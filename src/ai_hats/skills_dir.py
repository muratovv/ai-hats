"""Ref-counted materializer for directory-convention skill registries.

Extracted from ClineProvider (HATS-963/981) for providers whose harness
discovers skills from a project-scoped dir (``.gemini/skills/``,
``.cline/skills/``): the union of all live sessions' skills stays on disk;
a JSON marker keyed by session_id prevents parallel sessions from sweeping
each other's skills.  # HATS-993
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ai_hats_core import ResolvedComponent

MANAGED_MARKER = ".ai-hats-managed"
_LOCK_TIMEOUT = 30.0


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
