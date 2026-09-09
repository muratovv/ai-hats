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
from ai_hats.session_artifacts import BuiltArtifacts

from ..hook_dispatch import MANIFEST_VERSION
from .hook_server import socket_path
from .profile import PROFILE


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


__all__ = ["materialize_hook_manifest"]
