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

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR

from ..hook_channel import ENV_HOOK_SURFACE_TIMEOUT_MS, surface_timeout
from ai_hats.hook_collection import collect_runtime_hooks, resolve_skill_script
from ai_hats.paths import ai_hats_dir, session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts

MANIFEST_VERSION = 1

PLUGIN_ASSET = "ai-hats-hooks.mjs"


def plugin_source() -> str:
    """Return the packaged dispatcher plugin source."""
    return (
        resources.files("ai_hats.surfaces.opencode")
        .joinpath("plugin", PLUGIN_ASSET)
        .read_text(encoding="utf-8")
    )


def _manifest(
    project_dir: Path,
    result,
    session_id: str,
    *,
    skills_dir: Path,
    permission_rules: list[dict[str, str]],
) -> dict:
    hooks: dict[str, list[dict[str, str]]] = {}
    for event, entries in collect_runtime_hooks(result).items():
        event_hooks = hooks.setdefault(event, [])
        for skill_name, hook in entries:
            if resolve_skill_script(result, skill_name, hook.script) is None:
                continue
            event_hooks.append(
                {
                    "matcher": hook.matcher,
                    "command": str(skills_dir / skill_name / hook.script),
                    "tag": f"ai-hats:{skill_name}:{event}:{hook.matcher}:{Path(hook.script).stem}",
                }
            )
    return {
        "version": MANIFEST_VERSION,
        "session": {
            "id": session_id,
            "ai_hats_dir": str(ai_hats_dir(project_dir)),
        },
        "hooks": hooks,
        "permissions": permission_rules,
    }


def materialize_hook_manifest(
    project_dir: Path,
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
    cache_dir = session_cache_dir(project_dir, session_id)
    session_dir = cache_dir / "opencode"
    artifacts.port.mkdir(cache_dir)
    manifest_path = session_dir / "hooks.json"
    content = json.dumps(
        _manifest(
            project_dir,
            result,
            session_id,
            skills_dir=skills_dir,
            permission_rules=permission_rules,
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
    "plugin_source",
]
