"""Claude plugin-dir materialization (HATS-307, refined in HATS-294).

The spawned role's skills are materialized under the per-session cache
(``<cache_root>/sessions/<sid>/plugin/``, outside the project) and passed to
``claude`` via ``--plugin-dir``. Lives with the surface, not in core: the ``.claude-plugin``
manifest layout is claude's, not a shared concept (HATS-1211 review).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats_core import ResolvedComponent

from ai_hats.paths import (
    claude_plugin_manifest,
    claude_plugin_manifest_dir,
    claude_plugin_skills_dir,
)
from ai_hats.placeholders import expand_fsm_edges_token, expand_path_placeholders

if TYPE_CHECKING:
    from ai_hats.materialization import Materializer


def materialize_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    project_dir: Path,
    plugin_dir: Path,
    port: Materializer,
) -> Path:
    """Populate ``plugin_dir`` with the role's skills as a claude plugin.

    Recreated from scratch so the result is byte-stable for given inputs.
    Returns ``plugin_dir`` for caller convenience.
    """
    # The lock lives beside the target — never inside it — so the rmtree cannot
    # remove it, and the session-cache sweep takes it.
    lock_path = plugin_dir.parent / f"{plugin_dir.name}.lock"
    port.mkdir(plugin_dir.parent)
    with port.lock(lock_path):
        _rebuild_plugin_dir(role_name, skills, project_dir, plugin_dir, port)
    return plugin_dir


def _rebuild_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    project_dir: Path,
    plugin_dir: Path,
    port: Materializer,
) -> None:
    """Wipe-and-rebuild the plugin dir from scratch. Caller holds the lock."""
    port.remove_tree(plugin_dir)
    port.mkdir(plugin_dir)

    port.mkdir(claude_plugin_manifest_dir(plugin_dir))
    port.write_text(
        claude_plugin_manifest(plugin_dir),
        json.dumps({"name": f"ai-hats-{role_name}", "version": "0.0.0"}),
    )

    skills_root = claude_plugin_skills_dir(plugin_dir)
    port.mkdir(skills_root)

    for skill in skills:
        if not skill.source_path.is_dir():
            continue
        dest = skills_root / skill.name
        port.copy_tree(skill.source_path, dest)
        # HATS-380 parity: expand <ai_hats_dir>; HATS-1051: inject the FSM edge
        # table. Read from the SOURCE — under a PlanMaterializer the copy does
        # not exist, and reading the dest would silently drop this write.
        source_md = skill.source_path / "SKILL.md"
        if source_md.exists():
            original = source_md.read_text()
            rendered = expand_fsm_edges_token(
                expand_path_placeholders(original, project_dir), project_dir
            )
            if rendered != original:
                port.write_text(dest / "SKILL.md", rendered)
