"""Per-session plugin-dir materialization (HATS-307, refined in HATS-294).

Sessions spawned via ``Provider.build_session_prompt`` cannot see skills
that are absent from the project's ``.claude/skills/`` mirror (which today
reflects the *active* role, not the spawned role). To fix this for Claude,
the spawned role's skills are materialized into a directory under the
per-session cache (``<ai_hats_dir>/.cache/sessions/<sid>/plugin/``) and
passed to ``claude`` via ``--plugin-dir`` — a session-scoped, repeatable
flag that merges plugin skills into the default Skill registry under their
plain names.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import filelock

from ai_hats_core import ResolvedComponent
from .paths import (
    AI_HATS_MANAGED_MARKER,
    claude_dir,
    claude_plugin_manifest,
    claude_plugin_manifest_dir,
    claude_plugin_skills_dir,
    claude_skills_dir,
)
from .placeholders import expand_path_placeholders
from ai_hats_core.safe_delete import discard

# HATS-604: two callers can resolve the SAME per-session plugin dir (a
# session_id collision under high parallel load — see HATS-605 for the
# upstream fix). The rebuild below is multi-step and non-atomic
# (rmtree -> mkdir -> per-skill copytree); without serialisation concurrent
# processes shred each other (ENOTEMPTY / EEXIST / ENOENT). A per-dir
# advisory filelock makes the critical section mutually exclusive across
# processes (the worktree.py idiom). 30s is generous — the build is a
# sub-second filesystem op, so a timeout means a stuck/dead lock holder.
_LOCK_TIMEOUT = 30.0


def materialize_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    project_dir: Path,
    plugin_dir: Path,
) -> Path:
    """Populate ``plugin_dir`` with the role's skills as a claude plugin.

    HATS-294: caller provides the target ``plugin_dir`` (per-session cache).
    Directory is recreated from scratch — any prior contents are wiped so
    the result is byte-stable for given inputs (Fork E determinism).

    HATS-604: the rebuild runs under a per-dir ``filelock`` so concurrent
    callers sharing one ``plugin_dir`` serialise instead of racing. The lock
    file (``<plugin_dir>.lock``) lives beside the target — never inside it —
    so the ``rmtree`` cannot remove the lock, and it is swept with the rest
    of the session cache tree at session end.

    Returns ``plugin_dir`` for caller convenience.
    """
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = plugin_dir.parent / f"{plugin_dir.name}.lock"
    lock = filelock.FileLock(str(lock_path), timeout=_LOCK_TIMEOUT)
    try:
        with lock:
            _rebuild_plugin_dir(role_name, skills, project_dir, plugin_dir)
    except filelock.Timeout as exc:
        raise RuntimeError(
            f"plugin-dir materialization blocked >{_LOCK_TIMEOUT:.0f}s on "
            f"lock {lock_path} — a stuck ai-hats process likely holds it. "
            f"If safe, remove the lock file and retry."
        ) from exc
    return plugin_dir


def _rebuild_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    project_dir: Path,
    plugin_dir: Path,
) -> None:
    """Wipe-and-rebuild the plugin dir from scratch. Caller holds the lock."""
    if plugin_dir.exists():
        # Per-session plugin dir: rebuilt every session_start from compose.
        # Whitelist.
        shutil.rmtree(plugin_dir)  # safe-delete: ok session-plugin-rebuild
    plugin_dir.mkdir(parents=True)

    claude_plugin_manifest_dir(plugin_dir).mkdir()
    claude_plugin_manifest(plugin_dir).write_text(
        json.dumps({"name": f"ai-hats-{role_name}", "version": "0.0.0"})
    )

    skills_root = claude_plugin_skills_dir(plugin_dir)
    skills_root.mkdir()

    for skill in skills:
        if not skill.source_path.is_dir():
            continue
        dest = skills_root / skill.name
        shutil.copytree(skill.source_path, dest)
        # HATS-380 parity: expand <ai_hats_dir> in SKILL.md before the agent
        # reads it. Other assets (hooks, fixtures) are copied verbatim.
        skill_md = dest / "SKILL.md"
        if skill_md.exists():
            original = skill_md.read_text()
            expanded = expand_path_placeholders(original, project_dir)
            if expanded != original:
                skill_md.write_text(expanded)


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
            elif _dir_digest(candidate) == _dir_digest(plugin_skills_root / name):
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
    # Well-known publish artefacts — belt-and-suspenders (HATS-289).
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


def _dir_digest(root: Path) -> str:
    """sha256 over sorted (relpath, bytes) — equal digests ⇔ equal trees."""
    if not root.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
