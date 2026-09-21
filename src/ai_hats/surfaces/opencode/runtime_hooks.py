"""Session-scoped runtime-hook materialization for the OpenCode surface.

OpenCode discovers plugins from config layers, so the provider registers one
session-local dispatcher plugin through the ``plugin`` array of the
``OPENCODE_CONFIG`` file while the composed hook list stays in the ai-hats
session cache. The manifest schema (version 1) is shared with the cline and
codex surfaces.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR

from ..hook_channel import ENV_HOOK_SURFACE_TIMEOUT_MS, surface_timeout
from ai_hats.materialization import MaterializationEntry, describe_write_text

from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host

MANIFEST_VERSION = 1

PLUGIN_ASSET = "ai-hats-hooks.mjs"


def plugin_source() -> str:
    """Return the packaged dispatcher plugin source."""
    return (
        resources.files("ai_hats.surfaces.opencode")
        .joinpath("plugin", PLUGIN_ASSET)
        .read_text(encoding="utf-8")
    )


def plan_hooks(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_root: Path,
    permission_rules: list[dict[str, str]],
) -> tuple[MaterializationEntry, MaterializationEntry, dict[str, str]]:
    """The manifest entry, the dispatcher plugin entry and the pins, from the
    plan's runtime rows; the plugin is the package's own asset, so its bytes
    are in hand like a constant's."""
    session_dir = root / "opencode"
    manifest = describe_write_text(
        session_dir / "hooks.json",
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "session": {"id": root.name, "ai_hats_dir": str(layout.base)},
                "hooks": manifest_rows(composition, skills_root),
                "permissions": permission_rules,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    plugin = describe_write_text(session_dir / "plugin" / PLUGIN_ASSET, plugin_source())
    env = {
        ENV_SESSION_CACHE_DIR: str(root),
        ENV_AI_HATS_PYTHON: str(host.python),
        ENV_HOOK_SURFACE_TIMEOUT_MS: str(int(surface_timeout() * 1000)),
    }
    return manifest, plugin, env


__all__ = [
    "MANIFEST_VERSION",
    "PLUGIN_ASSET",
    "plan_hooks",
    "plugin_source",
]
