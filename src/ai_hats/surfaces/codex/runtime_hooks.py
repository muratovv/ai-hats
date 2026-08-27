"""Session-scoped runtime-hook materialization for the Codex surface.

Codex only discovers hooks from config layers.  The provider therefore passes
one stable dispatcher definition through ``-c`` while the composed hook list
stays in the ai-hats session cache.  Keeping session paths out of the command
is what makes Codex's reviewed hook hash reusable by concurrent sessions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from ai_hats.hook_collection import collect_runtime_hooks, resolve_skill_script
from ai_hats.paths import ai_hats_dir, session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts

from ..hook_channel import surface_timeout
from .hook_dispatcher import DISPATCHER_COMMAND

CODEX_HOOK_EVENTS = ("PreToolUse", "PermissionRequest", "PostToolUse")
MANIFEST_VERSION = 1


def _toml_string(value: str) -> str:
    """A JSON string is also a TOML basic string for this ASCII command."""
    return json.dumps(value, ensure_ascii=False)


def build_hook_cli_args() -> list[str]:
    """Return stable per-run Codex config overrides for the dispatcher.

    The definition contains no session/cache/project path and never opts out of
    hook trust.  Codex can therefore ask the user to review this exact command
    once instead of presenting a fresh hash for every ai-hats session.
    """
    # Omitting matcher is Codex's documented match-all form.  A literal `*`
    # looks like a glob but the matcher is regex-like and is not a valid
    # match-all expression on every CLI version.
    # Derived, never written by hand: bounding the dispatcher and the hook at
    # the same number is what made every timeout branch below unreachable and
    # left a killed chain with no verdict at all.
    handler = (
        '[{ hooks = [{ type = "command", command = '
        f"{_toml_string(DISPATCHER_COMMAND)}, timeout = {surface_timeout():.0f} }}] }}]"
    )
    args: list[str] = []
    for event in CODEX_HOOK_EVENTS:
        args.extend(["-c", f"hooks.{event}={handler}"])
    return args


def _manifest(
    project_dir: Path,
    result,
    session_id: str,
    *,
    skills_dir: Path,
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
                    "tag": f"ai-hats:{skill_name}:{event}:{hook.matcher}",
                }
            )
    return {
        "version": MANIFEST_VERSION,
        "session": {
            "id": session_id,
            "ai_hats_dir": str(ai_hats_dir(project_dir)),
            "skills_root": str(skills_dir),
        },
        "hooks": hooks,
    }


def materialize_hook_manifest(
    project_dir: Path,
    result,
    session_id: str,
    artifacts: BuiltArtifacts,
    *,
    skills_dir: Path,
) -> Path:
    """Write this composition's hook manifest and publish its runtime pins.

    Surface integration is intentionally a two-line surface-local call::

        path = materialize_hook_manifest(..., skills_dir=self.session_skills_root(...))
        artifacts.cli_args.extend(build_hook_cli_args())

    ``skills_dir`` must be the already-materialized session mirror.  Commands
    never point back at library sources or into the project root.
    """
    cache_dir = session_cache_dir(project_dir, session_id)
    artifacts.port.mkdir(cache_dir)
    path = cache_dir / "hooks.json"
    content = json.dumps(
        _manifest(project_dir, result, session_id, skills_dir=skills_dir),
        indent=2,
        sort_keys=True,
    )
    artifacts.port.write_text(path, content + "\n")
    artifacts.materialized.append(path)
    artifacts.extra_env[ENV_SESSION_CACHE_DIR] = str(cache_dir)
    artifacts.extra_env[ENV_AI_HATS_PYTHON] = sys.executable
    return path


__all__ = [
    "CODEX_HOOK_EVENTS",
    "MANIFEST_VERSION",
    "build_hook_cli_args",
    "materialize_hook_manifest",
]
