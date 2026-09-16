"""The session hook manifest claude's dispatcher reads, and the pins that reach it.

claude wires its gates from ``settings.json``, so the manifest is not what
delivers them — it is what the dispatcher reads once it holds the wiring. Both
are built from the one ``composed_rows`` pass, so neither can name a gate the
other does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_HOOK_SOCKET, ENV_SESSION_CACHE_DIR
from ai_hats.hook_collection import composed_rows
from ai_hats.materialization import MaterializationEntry, describe_write_text
from ai_hats.paths import claude_plugin_skills_dir
from ai_hats.session_artifacts import BuiltArtifacts

from ..hook_dispatch import MANIFEST_VERSION
from ..plan import CompositionPlan, Host, home_of, mirror_name
from .hook_server import socket_path
from .profile import PROFILE

Rows = dict[str, list[dict[str, str]]]


def plan_hooks(
    composition: CompositionPlan, root: Path, host: Host
) -> tuple[MaterializationEntry, Rows, dict[str, str]]:
    """The manifest entry, its rows and the pins, from the plan's runtime rows.

    A command points into the session's own skill mirror, derived from where
    the script lives in the library; the mirror is executable by construction
    (the tree sync keeps modes), so nothing is checked on disk.
    """
    rows: Rows = {}
    skills_root = claude_plugin_skills_dir(root / "plugin")
    for hook in composition.hooks.runtime:
        home = home_of(hook.run, composition.skills)
        if home is None:
            raise ValueError(
                f"runtime hook {hook.at.value}/{hook.matcher} runs {hook.run.path}, "
                "outside every composed skill"
            )
        skill, inside = home
        name = mirror_name(skill)
        rows.setdefault(hook.at.value, []).append(
            {
                "matcher": hook.matcher,
                "command": str(skills_root / name / inside),
                "tag": f"ai-hats:{name}:{hook.at.value}:{hook.matcher}:{inside.stem}",
            }
        )
    manifest = describe_write_text(
        PROFILE.manifest_path(root),
        json.dumps(
            {"version": MANIFEST_VERSION, "session": {"id": root.name}, "hooks": rows},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    env = {
        ENV_SESSION_CACHE_DIR: str(root),
        ENV_AI_HATS_PYTHON: str(host.python),
        ENV_HOOK_SOCKET: str(socket_path(root)),
    }
    return manifest, rows, env


def materialize_hook_manifest(
    result,
    artifacts: BuiltArtifacts,
    *,
    cache_dir: Path,
    session_id: str,
    skills_dir: Path,
) -> dict[str, list[dict[str, str]]]:
    """Write this composition's hook manifest and publish its runtime pins.

    Returns the rows it wrote, so the ``settings.json`` wiring is built from the
    same pass. The pins are the dispatcher's whole address, and its entry is a
    shell guard that refuses without them — a refusal the hatch cannot open,
    because the shell never reaches the python that honours it. So they are
    published here, beside the manifest they point at, and never in a separate
    step.
    """
    rows, notices = composed_rows(result, skills_dir, port=artifacts.port)
    artifacts.notices.extend(notices)
    path = PROFILE.manifest_path(cache_dir)
    artifacts.port.write_text(
        path,
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "session": {"id": session_id},
                "hooks": rows,
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
    return rows


__all__ = ["materialize_hook_manifest", "plan_hooks"]
