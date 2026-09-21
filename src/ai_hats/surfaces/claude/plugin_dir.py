"""The per-session claude plugin as plan entries.

The spawned role's skills are mirrored under the per-session cache
(``<cache_root>/sessions/<sid>/plugin/``, outside the project) and passed to
``claude`` via ``--plugin-dir``. Lives with the surface, not in core: the ``.claude-plugin``
manifest layout is claude's, not a shared concept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ai_hats.materialization import MaterializationEntry, describe_mkdir, describe_write_text
from ai_hats.paths import (
    claude_plugin_manifest,
    claude_plugin_manifest_dir,
    claude_plugin_skills_dir,
)

from .. import mirror
from ..mirror import mirror_entries
from ..plan import CompositionPlan


def plugin_name(identity: str) -> str:
    """The manifest's name: the identity as one token, since claude reads it as a label."""
    return "ai-hats-" + re.sub(r"[^A-Za-z0-9_-]+", "-", identity).strip("-")


def plan_plugin(composition: CompositionPlan, plugin_dir: Path) -> list[MaterializationEntry]:
    """The plugin as entries: manifest, then the skill mirror every surface derives."""
    skills_root = claude_plugin_skills_dir(plugin_dir)
    return [
        describe_mkdir(plugin_dir),
        describe_mkdir(claude_plugin_manifest_dir(plugin_dir)),
        describe_write_text(
            claude_plugin_manifest(plugin_dir),
            json.dumps({"name": plugin_name(composition.identity), "version": "0.0.0"}),
        ),
        describe_mkdir(skills_root),
        *mirror_entries(composition, skills_root),
    ]


def path_dirs(composition: CompositionPlan, plugin_dir: Path) -> list[Path]:
    """The mirrored directories the agent calls scripts from, in composition order."""
    return mirror.path_dirs(composition, claude_plugin_skills_dir(plugin_dir))
