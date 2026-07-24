"""Global hook dispatcher wiring for AGY surface."""

from __future__ import annotations

import json
from pathlib import Path

MANAGED_DISPATCHER_TAG = "ai-hats:global-dispatcher"
DISPATCHER_COMMAND = "ai-hats-hook-dispatcher"


def ensure_global_dispatcher_hook(settings_path: Path) -> bool:
    """Ensure ``ai-hats-hook-dispatcher`` is registered in AGY user-global settings.json.

    Idempotent: inspects ``settings_path`` (typically ``~/.gemini/antigravity-cli/settings.json``).
    If the managed dispatcher entries are already present in PreToolUse and PostToolUse, returns False.
    Otherwise, updates ``settings_path`` preserving all existing permissions/user fields and returns True.
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
    for event in ("PreToolUse", "PostToolUse"):
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

    if changed:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        content = (json.dumps(data, indent=2) + "\n").encode("utf-8")
        settings_path.write_bytes(content)

    return changed
