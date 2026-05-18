"""Per-spawn plugin-dir materialization for spawned sub-agent skills (HATS-307).

Sub-agent sessions spawned via ``Provider.build_override`` cannot see skills
that are absent from the project's ``.claude/skills/`` mirror (which reflects
the *active* role, not the spawned role). To fix this for Claude, the
spawned role's skills are materialized into an ephemeral directory under
``/tmp/`` and passed to ``claude`` via ``--plugin-dir`` — a session-scoped,
repeatable flag that merges plugin skills into the default Skill registry
under their plain names.

The primary user session is unaffected — it still relies on the persistent
``.claude/skills/`` mirror written by ``Assembler.set_role``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .composer import ResolvedComponent
from .placeholders import expand_path_placeholders


def materialize_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    project_dir: Path,
) -> Path:
    """Create an ephemeral plugin directory with the role's skills.

    Returns the plugin-dir path. The caller owns cleanup
    (``shutil.rmtree(path, ignore_errors=True)`` in a ``try/finally``).
    """
    plugin_dir = Path(tempfile.mkdtemp(prefix="ai-hats-plugin-"))

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
