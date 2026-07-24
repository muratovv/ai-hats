"""Global Hook Dispatcher for AGY surface (HATS-1166).

Executes session-specific hooks from `<project_dir>/.agent/ai-hats/.cache/sessions/<session_id>/hooks.json`
when invoked by the global AGY hook registered in `~/.gemini/antigravity-cli/settings.json`.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path


def dispatch_hook(event: str, tool_name: str | None = None) -> int:
    """Read session hooks manifest and execute matching hooks for this event."""
    session_id = os.environ.get("AI_HATS_SESSION_ID")
    project_dir_str = os.environ.get("AI_HATS_PROJECT_DIR")

    if not session_id or not project_dir_str:
        # Standalone agy run outside ai-hats session — no-op exit 0
        return 0

    project_dir = Path(project_dir_str)
    hooks_file = (
        project_dir
        / ".agent"
        / "ai-hats"
        / ".cache"
        / "sessions"
        / session_id
        / "hooks.json"
    )

    if not hooks_file.is_file():
        return 0

    try:
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0

    if not isinstance(data, dict):
        return 0

    event_hooks = data.get(event, [])
    if not isinstance(event_hooks, list):
        return 0

    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except (OSError, AttributeError):
        stdin_data = ""

    for hook in event_hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if not command or not isinstance(command, str):
            continue

        matcher = hook.get("matcher", "*")
        if tool_name and matcher != "*" and tool_name not in matcher.split("|"):
            continue

        try:
            res = subprocess.run(
                command,
                shell=True,
                input=stdin_data,
                text=True,
                capture_output=True,
                env=os.environ.copy(),
            )
            if res.stdout:
                sys.stdout.write(res.stdout)
            if res.stderr:
                sys.stderr.write(res.stderr)

            if res.returncode != 0:
                return res.returncode
        except Exception as err:
            sys.stderr.write(f"ai-hats-hook-dispatcher error executing {command}: {err}\n")
            return 1

    return 0


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
    tool_name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AGY_TOOL_NAME")
    sys.exit(dispatch_hook(event, tool_name))


if __name__ == "__main__":
    main()
