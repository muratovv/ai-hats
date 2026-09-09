"""Legacy claude skills-mirror cleanup and collision detection (HATS-307/294).

Materialization moved to ``surfaces/claude/plugin_dir.py`` (HATS-1211): it is
claude's own layout, not a core concept. What stays here is the legacy-mirror
sweep and the auto-discovery collision report.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .fs_digest import dir_digest
from .paths import (
    AI_HATS_MANAGED_MARKER,
    claude_dir,
    claude_skills_dir,
)
from ai_hats_core.safe_delete import discard


@dataclass(frozen=True)
class SkillCollision:
    """One composed skill also present in a Claude Code auto-discovery dir (HATS-901).

    ``scope`` is the heal partition key (HATS-931): a ``"project"`` collision
    always auto-heals at session start — project `.claude/skills` is ai-hats-owned,
    not a user-authoring surface — while a ``"home"`` collision only warns (HATS-465).

    ``verdict`` refines the warn wording for home collisions: ``"identical"`` —
    byte-equal to the plugin copy; ``"managed"`` — marker-listed; ``"differs"`` —
    a drifted or user copy to review.
    """

    name: str
    path: Path
    verdict: str
    scope: str


def duplicate_skill_registrations(
    skill_names: list[str],
    *,
    project_dir: Path,
    plugin_skills_root: Path,
    home: Path,
) -> list[SkillCollision]:
    """Detect composed skills that will double-register this session (HATS-901).

    Claude Code registers skills by name, so a same-name dir under
    ``<home>/.claude/skills/`` or ``<project>/.claude/skills/`` duplicates
    the session-plugin delivery — the collision condition is exact name
    equality, no ownership proof needed.
    """
    collisions: list[SkillCollision] = []
    scopes = (
        ("home", claude_skills_dir(home)),
        ("project", claude_skills_dir(project_dir)),
    )
    for scope, scope_dir in scopes:
        if not scope_dir.is_dir():
            continue
        managed = _marker_names(scope_dir / ".ai-hats-managed")
        for name in skill_names:
            candidate = scope_dir / name
            if not candidate.is_dir():
                continue
            if name in managed:
                verdict = "managed"
            elif dir_digest(candidate) == dir_digest(plugin_skills_root / name):
                verdict = "identical"
            else:
                verdict = "differs"
            collisions.append(
                SkillCollision(name=name, path=candidate, verdict=verdict, scope=scope)
            )
    return collisions


def drop_legacy_skills_mirror(project_dir: Path, names: Iterable[str] | None = None) -> list[str]:
    """Discard a stale ai-hats `.claude/skills/` export mirror (HATS-901, HATS-931).

    Victims = marker-listed names (when `.ai-hats-managed` exists) ∪ ``names`` —
    HATS-931 passes the project-scope collision names so pre-marker (marker-less)
    mirrors heal too; ownership proof is the composed-skill name match (see task
    card). Returns the names removed. Every candidate is re-validated as a plain
    child; a ``skills_dir`` that is/links to ``~/.claude/skills`` is never swept
    (HATS-465).
    """
    skills_dir = claude_skills_dir(project_dir)
    marker = skills_dir / ".ai-hats-managed"
    has_marker = marker.is_file()
    victims = set(_marker_names(marker))
    if names:
        victims |= set(names)
    if not victims:
        return []
    if skills_dir.is_symlink():
        return []
    try:
        if skills_dir.resolve() == claude_skills_dir(Path.home()).resolve():
            return []
    except OSError:
        return []
    removed: list[str] = []
    for name in sorted(victims):
        if not _is_plain_child(skills_dir, name):
            continue
        victim = skills_dir / name
        if not victim.exists() and not victim.is_symlink():
            continue
        discard(victim, reason="claude-legacy-skills-mirror", project_dir=project_dir)
        removed.append(name)
    if has_marker:
        discard(marker, reason="claude-legacy-skills-mirror", project_dir=project_dir)
    try:
        if not any(skills_dir.iterdir()):
            skills_dir.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        pass
    return removed


def drop_legacy_claude_publish(project_dir: Path) -> list[str]:
    """Discard pre-HATS-289 ``.claude/`` publish artefacts (manifest-listed +
    well-known belt-and-suspenders set).

    Shared sweep procedure for ``owner_key=claude-publish`` (HATS-905): the
    scaffold-migration path and the generic unclaimed-marker sweeper call the
    same code. Returns the relative names actually removed.
    """
    base = claude_dir(project_dir)
    if not base.is_dir():
        return []
    manifest = base / AI_HATS_MANAGED_MARKER
    removed: list[str] = []
    for rel in sorted(_marker_names(manifest)):
        if rel.startswith("skills/"):
            continue
        if not _is_safe_relative(base, rel):
            continue
        target = base / rel
        if not target.exists() and not target.is_symlink():
            continue
        discard(target, reason="claude-legacy-publish", project_dir=project_dir)
        removed.append(rel)
    # Well-known publish artefacts — belt-and-suspenders.
    for rel in ("CLAUDE.md", "priorities.md", "role.md", "skills_index.md", "traits", "rules"):
        target = base / rel
        if not target.exists():
            continue
        try:
            discard(target, reason="claude-legacy-publish", project_dir=project_dir)
            removed.append(rel)
        except OSError:
            continue
    if manifest.is_file():
        discard(manifest, reason="claude-legacy-manifest", project_dir=project_dir)
    try:
        if not any(base.iterdir()):
            base.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        pass
    return removed


def drop_legacy_root_skills_mirrors(project_dir: Path) -> list[str]:
    """Discard pre-HATS-1165 root skill & artifact mirrors (.agy/skills, .gemini/skills, .cline/skills, .agents).

    Clean-root role materialization (HATS-1165) moves all session materializations
    strictly inside the per-session cache dir (`session_cache_dir`). This function
    sweeps legacy root-level materialization directories left over in project roots.
    """
    candidates = (
        project_dir / ".agy" / "skills",
        project_dir / ".agy" / "rules",
        project_dir / ".gemini" / "skills",
        project_dir / ".gemini" / "rules",
        project_dir / ".cline" / "skills",
        project_dir / ".cline" / "plugins",
        project_dir / ".cline" / "rules",
        project_dir / ".clinerules",
        project_dir / ".agents",
    )
    removed: list[str] = []
    for cand in candidates:
        if not cand.exists() and not cand.is_symlink():
            continue
        try:
            rel = str(cand.relative_to(project_dir))
        except ValueError:
            continue
        discard(cand, reason="legacy-root-skills-mirror", project_dir=project_dir)
        removed.append(rel)
        parent = cand.parent
        if parent != project_dir and parent.is_dir():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()  # safe-delete: ok empty-dir
            except OSError:
                pass

    parents = (
        project_dir / ".agy",
        project_dir / ".gemini",
        project_dir / ".cline",
    )
    for p in parents:
        if p != project_dir and p.is_dir():
            try:
                if not any(p.iterdir()):
                    p.rmdir()  # safe-delete: ok empty-dir
            except OSError:
                pass

    return removed


def _is_safe_relative(base_dir: Path, name: str) -> bool:
    """:func:`_is_plain_child` generalized to nested relative entries
    (HATS-905: githooks/publish manifests list ``a/b`` paths); victims must
    resolve strictly inside ``base_dir``."""
    if not name or "\\" in name:
        return False
    parts = Path(name).parts
    if Path(name).is_absolute() or "." in parts or ".." in parts:
        return False
    victim = base_dir / name
    if victim.is_symlink():
        return True  # discard unlinks the link only; the target survives
    try:
        return base_dir.resolve() in victim.resolve().parents
    except OSError:
        return False


def _is_plain_child(skills_dir: Path, name: str) -> bool:
    """HATS-907 P1: a marker line names a victim only as a single path
    component — traversal/absolute lines in a committable marker are inert."""
    if name in (".", "..") or "/" in name or "\\" in name or Path(name).is_absolute():
        return False
    victim = skills_dir / name
    if victim.is_symlink():
        return True  # discard unlinks the link only; the target survives
    try:
        return victim.resolve().parent == skills_dir.resolve()
    except OSError:
        return False


def _marker_names(marker: Path) -> frozenset[str]:
    if not marker.is_file():
        return frozenset()
    return frozenset(
        line.strip()
        for line in marker.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
