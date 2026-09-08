"""The session hook manifest claude's dispatcher reads, and the pins that reach it.

claude wires its gates from ``settings.json``, so the manifest is not what
delivers them — it is what the dispatcher reads once it holds the wiring. Both
are built from :func:`composed_rows`, so neither can name a gate the other does
not. Claude's own copy of codex's writer on purpose: the shared one would drag
in the silent ``resolve_skill_script -> None`` drop (HATS-1862).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_HOOK_SOCKET, ENV_SESSION_CACHE_DIR
from ai_hats.hook_collection import collect_runtime_hooks, resolve_skill_script
from ai_hats.session_artifacts import BuiltArtifacts

from ..hook_dispatch import MANIFEST_VERSION
from .hook_server import socket_path
from .profile import PROFILE


def composed_rows(result, skills_dir: Path) -> dict[str, list[dict[str, str]]]:
    """``{event: [row, ...]}`` this composition declares, in manifest shape.

    The one producer behind both the manifest and the ``settings.json`` entries.
    Commands are absolute and point into the session's own skill mirror, so a
    hook runs beside the files its skill ships (HATS-1268) from any cwd
    (HATS-615). A hook whose script does not resolve is skipped exactly as the
    wiring has always skipped it — HATS-1862 owns making that loud.
    """
    rows: dict[str, list[dict[str, str]]] = {}
    if result is None:
        return rows
    for event, entries in collect_runtime_hooks(result).items():
        for skill_name, hook in entries:
            if resolve_skill_script(result, skill_name, hook.script) is None:
                continue
            rows.setdefault(event, []).append(
                {
                    "matcher": hook.matcher,
                    "command": str(skills_dir / skill_name / hook.script),
                    "tag": f"ai-hats:{skill_name}:{event}:{hook.matcher}:{Path(hook.script).stem}",
                }
            )
    return rows


def materialize_hook_manifest(
    result,
    artifacts: BuiltArtifacts,
    *,
    cache_dir: Path,
    session_id: str,
    skills_dir: Path,
) -> Path:
    """Write this composition's hook manifest and publish its runtime pins.

    The pins are the dispatcher's whole address, and its entry is a shell guard
    that refuses without them — a refusal the hatch cannot open, because the
    shell never reaches the python that honours it. So they are published here,
    beside the manifest they point at, and never in a separate step.
    """
    path = PROFILE.manifest_path(cache_dir)
    artifacts.port.write_text(
        path,
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "session": {"id": session_id},
                "hooks": composed_rows(result, skills_dir),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    artifacts.materialized.append(path)
    artifacts.extra_env[ENV_SESSION_CACHE_DIR] = str(cache_dir)
    artifacts.extra_env[ENV_AI_HATS_PYTHON] = sys.executable
    # Published by the writer so the server and the entry cannot disagree about
    # where to listen; absent or stale, the entry spawns instead.
    artifacts.extra_env[ENV_HOOK_SOCKET] = str(socket_path(cache_dir))
    return path


__all__ = ["composed_rows", "materialize_hook_manifest"]
