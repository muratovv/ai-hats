"""Session-scoped runtime-hook delivery for the Cline surface."""

from __future__ import annotations

import json
import sys
from importlib.resources import as_file, files
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from .profile import PROFILE
from ai_hats.hook_collection import collect_runtime_hooks, resolve_skill_script
from ai_hats.session_artifacts import BuiltArtifacts

CLINE_HOOK_EVENTS: tuple[str, ...] = PROFILE.native_events
MANIFEST_VERSION = 1


def _manifest(layout: ProjectLayout, result, session_id: str, *, skills_dir: Path) -> dict:
    hooks: dict[str, list[dict[str, str]]] = {}
    collected = collect_runtime_hooks(result)
    for event in CLINE_HOOK_EVENTS:
        for skill_name, hook in collected.get(event, []):
            if resolve_skill_script(result, skill_name, hook.script) is None:
                continue
            hooks.setdefault(event, []).append(
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
            "ai_hats_dir": str(layout.base),
        },
        "hooks": hooks,
    }


def materialize_runtime_hooks(
    layout: ProjectLayout,
    result,
    session_id: str,
    artifacts: BuiltArtifacts,
    *,
    skills_dir: Path,
) -> Path | None:
    """Materialize the composed Cline chain; return its hook directory."""
    manifest = _manifest(layout, result, session_id, skills_dir=skills_dir)
    if not manifest["hooks"]:
        return None

    cache_dir = layout.cache.session(session_id)
    hooks_dir = cache_dir / "hooks"
    artifacts.port.mkdir(cache_dir)
    artifacts.port.remove_tree(hooks_dir)
    with as_file(files("ai_hats.surfaces.cline").joinpath("hooks")) as source:
        artifacts.port.copy_tree(source, hooks_dir)

    manifest_path = cache_dir / "hooks.json"
    artifacts.port.write_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    artifacts.materialized.extend((hooks_dir, manifest_path))
    artifacts.extra_env[ENV_SESSION_CACHE_DIR] = str(cache_dir)
    artifacts.extra_env[ENV_AI_HATS_PYTHON] = sys.executable
    return hooks_dir


__all__ = ["CLINE_HOOK_EVENTS", "MANIFEST_VERSION", "materialize_runtime_hooks"]
