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

from .composer import ResolvedComponent
from .placeholders import expand_path_placeholders


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
    Returns ``plugin_dir`` for caller convenience.
    """
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
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

    return plugin_dir
