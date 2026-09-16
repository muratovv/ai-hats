"""Claude plugin-dir materialization.

The spawned role's skills are materialized under the per-session cache
(``<cache_root>/sessions/<sid>/plugin/``, outside the project) and passed to
``claude`` via ``--plugin-dir``. Lives with the surface, not in core: the ``.claude-plugin``
manifest layout is claude's, not a shared concept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

from ai_hats_core import ResolvedComponent

from ai_hats.materialization import (
    MaterializationEntry,
    WriteKind,
    describe_mkdir,
    describe_write_text,
)
from ai_hats.paths import (
    claude_plugin_manifest,
    claude_plugin_manifest_dir,
    claude_plugin_skills_dir,
)
from ai_hats.placeholders import expand_fsm_edges_token, expand_path_placeholders

from ..plan import CompositionPlan, mirror_name

if TYPE_CHECKING:
    from ai_hats.materialization import Materializer


def plugin_name(identity: str) -> str:
    """The manifest's name: the identity as one token, since claude reads it as a label."""
    return "ai-hats-" + re.sub(r"[^A-Za-z0-9_-]+", "-", identity).strip("-")


def plan_plugin(composition: CompositionPlan, plugin_dir: Path) -> list[MaterializationEntry]:
    """The plugin as entries: manifest, then each skill's tree with its
    document written over the copy. No wipe — a session root is its own, and
    the tree sync leaves nothing stale behind."""
    entries = [
        describe_mkdir(plugin_dir),
        describe_mkdir(claude_plugin_manifest_dir(plugin_dir)),
        describe_write_text(
            claude_plugin_manifest(plugin_dir),
            json.dumps({"name": plugin_name(composition.identity), "version": "0.0.0"}),
        ),
    ]
    skills_root = claude_plugin_skills_dir(plugin_dir)
    entries.append(describe_mkdir(skills_root))
    for skill in composition.skills:
        dest = skills_root / mirror_name(skill)
        entries.append(
            MaterializationEntry(
                kind=WriteKind.COPY_TREE,
                target=dest,
                source=skill.path,
                tree_digest=skill.content_digest,
            )
        )
        if skill.document is not None:
            entries.append(describe_write_text(dest / "SKILL.md", skill.document))
    return entries


def path_dirs(composition: CompositionPlan, plugin_dir: Path) -> list[Path]:
    """The mirrored directories the agent calls scripts from, in composition order."""
    skills_root = claude_plugin_skills_dir(plugin_dir)
    return [skills_root / mirror_name(s) / d for s in composition.skills for d in s.on_path]


def materialize_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    layout: ProjectLayout,
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
        _rebuild_plugin_dir(role_name, skills, layout, plugin_dir, port)
    return plugin_dir


def _rebuild_plugin_dir(
    role_name: str,
    skills: list[ResolvedComponent],
    layout: ProjectLayout,
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
        # Expand <ai_hats_dir> for parity; inject the FSM edge
        # table. Read from the SOURCE — under a PlanMaterializer the copy does
        # not exist, and reading the dest would silently drop this write.
        source_md = skill.source_path / "SKILL.md"
        if source_md.exists():
            original = source_md.read_text()
            rendered = expand_fsm_edges_token(expand_path_placeholders(original, layout), layout)
            if rendered != original:
                port.write_text(dest / "SKILL.md", rendered)
