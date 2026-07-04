"""ai-hats.yaml schema-version migrations (v1 → v4, run by ``ProjectConfig.from_yaml``)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from ai_hats_core import atomic_write_text

logger = logging.getLogger(__name__)


def _migrate_v1_to_v2(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v1 → v2: merge profile.json into ai-hats.yaml.

    Runs once when a v1 ai-hats.yaml is loaded. Merges active_role, provider,
    and feedback from adjacent profile.json (if present), writes the unified
    YAML, and renames profile.json to profile.json.bak.
    """
    profile_path = yaml_path.parent / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text())
            if profile.get("provider"):
                data["provider"] = profile["provider"]
            data["active_role"] = profile.get("active_role", "")
            if "feedback" in profile:
                data["feedback"] = profile["feedback"]
            profile_path.rename(profile_path.with_suffix(".json.bak"))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt profile.json — skip, use YAML as-is

    data["schema_version"] = 2
    atomic_write_text(yaml_path, yaml.dump(data, default_flow_style=False, allow_unicode=True))
    logger.info("Migrated profile.json → ai-hats.yaml (schema v2)")
    return data


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v2 → v3 (HATS-285).

    v3 introduces the layered canonical layout (.agent/ai-hats/) and the
    `./CLAUDE.md` scaffold-as-asset. The yaml itself only needs a version
    bump — the filesystem cleanup (stripping the legacy uppercase block
    from `./CLAUDE.md`) lives in `Assembler._migrate_claude_md_to_v3`,
    which runs at the start of `init`/`set_role`/`bump`.
    """
    data["schema_version"] = 3
    return data


def _migrate_v3_to_v4(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v3 → v4 (HATS-316).

    v4 introduces the unified `<ai_hats_dir>` layout: all framework-managed
    artefacts (sessions/, tracker/, library/, STATE.md, ...) live under a
    single configurable root. This migration writes the canonical default
    `.agent/ai-hats` to disk explicitly so users see the configurable path
    in their `ai-hats.yaml`. Actual file moves happen in HATS-312/313/314.
    """
    if "ai_hats_dir" not in data:
        data["ai_hats_dir"] = ".agent/ai-hats"
    data["schema_version"] = 4
    atomic_write_text(yaml_path, yaml.dump(data, default_flow_style=False, allow_unicode=True))
    logger.info("Migrated ai-hats.yaml to schema v4 (added ai_hats_dir)")
    return data
