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
from dataclasses import dataclass
from pathlib import Path

import filelock

from .composer import ResolvedComponent
from .paths import claude_skills_dir
from .placeholders import expand_path_placeholders
from .safe_delete import discard

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

    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": f"ai-hats-{role_name}", "version": "0.0.0"})
    )

    skills_root = plugin_dir / "skills"
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

    ``verdict``: ``"identical"`` — byte-equal to the plugin copy, safe to
    remove; ``"managed"`` — marker-listed stale ai-hats mirror (session start
    auto-heals the project scope, HATS-907; home is user-owned, HATS-465);
    ``"differs"`` — stale copy or user-authored, user must review.

    ``scope``: ``"home"`` or ``"project"`` — the heal partition key (HATS-907).
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


def drop_legacy_skills_mirror(project_dir: Path) -> list[str]:
    """Discard the pre-HATS-294 `.claude/skills/` export mirror (HATS-901).

    Returns the marker-listed skill names actually removed (HATS-907: the
    session-start heal note needs them). Runs unattended at session start, so
    marker content is untrusted — only validated child names are victims, and
    a ``skills_dir`` that is (or links to) the user-level ``~/.claude/skills``
    is never swept (HATS-465: ai-hats never wrote there).
    """
    skills_dir = claude_skills_dir(project_dir)
    marker = skills_dir / ".ai-hats-managed"
    if not marker.is_file():
        return []
    if skills_dir.is_symlink():
        return []
    try:
        if skills_dir.resolve() == claude_skills_dir(Path.home()).resolve():
            return []
    except OSError:
        return []
    removed: list[str] = []
    for name in sorted(_marker_names(marker)):
        victim = skills_dir / name
        if not _is_plain_child(skills_dir, name):
            continue
        if not victim.exists() and not victim.is_symlink():
            continue
        discard(victim, reason="claude-legacy-skills-mirror", project_dir=project_dir)
        removed.append(name)
    discard(marker, reason="claude-legacy-skills-mirror", project_dir=project_dir)
    try:
        if not any(skills_dir.iterdir()):
            skills_dir.rmdir()  # safe-delete: ok empty-dir
    except OSError:
        pass
    return removed


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
