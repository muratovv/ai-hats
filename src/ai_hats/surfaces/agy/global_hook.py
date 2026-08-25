"""Global hook dispatcher wiring for AGY surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_hats.materialization import Materializer

MANAGED_DISPATCHER_TAG = "ai-hats:global-dispatcher"
DISPATCHER_COMMAND = (
    'sh -c \'if [ -n "$AI_HATS_SESSION_ID" ] && [ -x "$AI_HATS_PYTHON" ]; '
    'then "$AI_HATS_PYTHON" -m ai_hats.surfaces.agy.hook_dispatcher "$@"; fi\' sh'
)


def ensure_global_dispatcher_hook(settings_path: Path, port: "Materializer") -> bool:
    """Ensure universal fail-safe dispatcher is registered in AGY user-global settings.json.

    Idempotent and version-safe:
    - Registers a static, fail-safe shell dispatcher command in ``settings_path``.
    - Uses ``$AI_HATS_PYTHON`` exported by the active session to invoke the exact Python interpreter
      belonging to that session's venv.
    - Zero cross-venv clobbering: different venvs write the identical static command string.
    - Standalone agy runs (without ``AI_HATS_SESSION_ID``) exit 0 immediately with zero overhead.
    """
    data: dict = {}
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    if not isinstance(data, dict):
        data = {}

    hooks_root = data.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        hooks_root = {}
        data["hooks"] = hooks_root

    changed = False
    for event in ("PreToolUse", "PostToolUse", "Stop", "Notification", "PostInvocation"):
        event_list = hooks_root.setdefault(event, [])
        if not isinstance(event_list, list):
            event_list = []
            hooks_root[event] = event_list

        desired_entry = {
            "matcher": "*",
            "command": DISPATCHER_COMMAND,
            "_ai_hats_managed": MANAGED_DISPATCHER_TAG,
        }

        # Check if already correctly present
        already_present = False
        for i, entry in enumerate(event_list):
            if isinstance(entry, dict) and entry.get("_ai_hats_managed") == MANAGED_DISPATCHER_TAG:
                already_present = True
                if entry != desired_entry:
                    event_list[i] = desired_entry
                    changed = True
                break

        if not already_present:
            event_list.append(desired_entry)
            changed = True

    if not changed:
        return False

    # A session mutating a user-owned file is news — the port records the key diff.
    return port.merge_json(settings_path, data)
