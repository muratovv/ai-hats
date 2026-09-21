"""The session hook manifest claude's dispatcher reads, and the pins that reach it.

claude wires its gates from ``settings.json``, so the manifest is not what
delivers them — it is what the dispatcher reads once it holds the wiring. Both
are built from the one ``manifest_rows`` pass, so neither can name a gate the
other does not.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_HOOK_SOCKET, ENV_SESSION_CACHE_DIR
from ai_hats.materialization import MaterializationEntry, describe_write_text
from ai_hats.paths import claude_plugin_skills_dir

from ..hook_dispatch import MANIFEST_VERSION
from ..mirror import Rows, manifest_rows
from ..plan import CompositionPlan, Host
from .hook_server import socket_path
from .profile import PROFILE


def plan_hooks(
    composition: CompositionPlan, root: Path, host: Host
) -> tuple[MaterializationEntry, Rows, dict[str, str]]:
    """The manifest entry, its rows and the pins, from the plan's runtime rows.

    A command points into the session's own skill mirror, derived from where
    the script lives in the library; the mirror is executable by construction
    (the tree sync keeps modes), so nothing is checked on disk. The pins are
    the dispatcher's whole address, published beside the manifest they point at.
    """
    rows = manifest_rows(composition, claude_plugin_skills_dir(root / "plugin"))
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


__all__ = ["plan_hooks"]
