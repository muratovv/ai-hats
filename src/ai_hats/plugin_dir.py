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

import json
import shutil
from pathlib import Path

import filelock

from .composer import ResolvedComponent
from .placeholders import expand_path_placeholders

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
