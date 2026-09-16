"""Session-scoped runtime-hook materialization for the OpenCode surface.

OpenCode discovers plugins from config layers, so the provider registers one
session-local dispatcher plugin through the ``plugin`` array of the
``OPENCODE_CONFIG`` file while the composed hook list stays in the ai-hats
session cache. The manifest schema (version 1) is shared with the cline and
codex surfaces.
"""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR

from ..hook_channel import ENV_HOOK_SURFACE_TIMEOUT_MS, surface_timeout
from ai_hats.hook_collection import composed_rows
from ai_hats.materialization import MaterializationEntry, describe_write_text
from ai_hats.session_artifacts import BuiltArtifacts

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


def _manifest(
    layout: ProjectLayout,
    result,
    session_id: str,
    *,
    skills_dir: Path,
    permission_rules: list[dict[str, str]],
    artifacts: BuiltArtifacts,
) -> dict:
    hooks, notices = composed_rows(result, skills_dir, port=artifacts.port)
    artifacts.notices.extend(notices)
    return {
        "version": MANIFEST_VERSION,
        "session": {
            "id": session_id,
            "ai_hats_dir": str(layout.base),
        },
        "hooks": hooks,
        "permissions": permission_rules,
    }


def materialize_hook_manifest(
    layout: ProjectLayout,
    result,
    session_id: str,
    artifacts: BuiltArtifacts,
    *,
    skills_dir: Path,
    permission_rules: list[dict[str, str]],
) -> tuple[Path, Path]:
    """Write this composition's hook manifest and dispatcher plugin.

    Returns ``(manifest_path, plugin_path)``; the provider registers the plugin
    through the session config's ``plugin`` array and pins the cache dir env.

    ``skills_dir`` must be the already-materialized session mirror. Commands
    never point back at library sources or into the project root.
    """
    cache_dir = layout.cache.session(session_id)
    session_dir = cache_dir / "opencode"
    artifacts.port.mkdir(cache_dir)
    manifest_path = session_dir / "hooks.json"
    content = json.dumps(
        _manifest(
            layout,
            result,
            session_id,
            skills_dir=skills_dir,
            permission_rules=permission_rules,
            artifacts=artifacts,
        ),
        indent=2,
        sort_keys=True,
    )
    artifacts.port.write_text(manifest_path, content + "\n")
    artifacts.materialized.append(manifest_path)

    plugin_path = session_dir / "plugin" / PLUGIN_ASSET
    artifacts.port.write_text(plugin_path, plugin_source())
    artifacts.materialized.append(plugin_path)

    artifacts.extra_env[ENV_SESSION_CACHE_DIR] = str(cache_dir)
    # The plugin is JavaScript and cannot judge a call; it shells out to the
    # dispatcher, which needs the interpreter this session was built with.
    artifacts.extra_env[ENV_AI_HATS_PYTHON] = sys.executable
    # The plugin's kill bound, derived rather than written into the asset: it is
    # copied verbatim, so a literal there could not track the chain's budget and
    # would bound the dispatcher below it as soon as the budget was raised.
    artifacts.extra_env[ENV_HOOK_SURFACE_TIMEOUT_MS] = str(int(surface_timeout() * 1000))
    return manifest_path, plugin_path


__all__ = [
    "MANIFEST_VERSION",
    "PLUGIN_ASSET",
    "materialize_hook_manifest",
    "plan_hooks",
    "plugin_source",
]
