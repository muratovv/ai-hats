"""Global Hook Dispatcher for AGY surface (HATS-1166).

Located entirely inside `ai_hats_agy` surface package.
Executes session-specific hooks from `<session_cache_dir>/hooks.json` when invoked
by the global AGY hook registered in `~/.gemini/antigravity-cli/settings.json`.

The cache dir arrives pre-resolved in `AI_HATS_SESSION_CACHE_DIR` (HATS-1398):
this process runs on every tool call, so it must not import ai-hats to re-derive
a path the session builder already knew.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path


def _session_hooks_file() -> Path | None:
    """This session's hooks manifest, from the dir the builder pinned (HATS-1398).

    An ai-hats session without the pin predates the cache move; say so rather
    than exit 0, which reads exactly like "no hooks configured".
    """
    pinned = os.environ.get("AI_HATS_SESSION_CACHE_DIR")
    if pinned:
        return Path(pinned) / "hooks.json"
    sys.stderr.write(
        "ai-hats-hook-dispatcher: AI_HATS_SESSION_CACHE_DIR unset — this session "
        "predates HATS-1398 and its hooks are unreachable; restart it.\n"
    )
    return None


def dispatch_hook(event_arg: str | None = None, tool_name: str | None = None) -> int:
    """Read session hooks manifest and execute matching hooks for this event."""
    session_id = os.environ.get("AI_HATS_SESSION_ID")
    project_dir_str = os.environ.get("AI_HATS_PROJECT_DIR")

    if not session_id or not project_dir_str:
        # Standalone agy run outside ai-hats session — no-op exit 0
        return 0

    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except (OSError, AttributeError):
        stdin_data = ""

    # Resolve event name from arg, stdin JSON payload, or fallback
    event = event_arg
    if not event:
        if stdin_data:
            try:
                payload = json.loads(stdin_data)
                if isinstance(payload, dict):
                    event = (
                        payload.get("hook_event_name")
                        or payload.get("event_name")
                        or payload.get("event")
                        or payload.get("hook")
                    )
            except (OSError, ValueError):
                pass
    if not event:
        event = "PreToolUse"

    hooks_file = _session_hooks_file()

    data: dict = {}
    if hooks_file and hooks_file.is_file():
        try:
            data = json.loads(hooks_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    event_hooks: list[dict] = []
    if isinstance(data, dict):
        raw_session_hooks = data.get(event, [])
        if isinstance(raw_session_hooks, list):
            event_hooks.extend(h for h in raw_session_hooks if isinstance(h, dict))
        elif isinstance(raw_session_hooks, dict):
            event_hooks.append(raw_session_hooks)

    user_hooks_file = Path.home() / ".gemini" / "config" / "hooks.json"
    if user_hooks_file.is_file():
        try:
            user_data = json.loads(user_hooks_file.read_text(encoding="utf-8"))
            if isinstance(user_data, dict):
                raw_user_hooks = user_data.get(event, [])
                if isinstance(raw_user_hooks, list):
                    event_hooks.extend(h for h in raw_user_hooks if isinstance(h, dict))
                elif isinstance(raw_user_hooks, dict):
                    event_hooks.append(raw_user_hooks)
        except (OSError, ValueError):
            pass

    for hook in event_hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if not command and "hooks" in hook and isinstance(hook["hooks"], list):
            for inner in hook["hooks"]:
                if isinstance(inner, dict) and "command" in inner:
                    command = inner["command"]
                    break
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
    event_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    tool_name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AGY_TOOL_NAME")
    sys.exit(dispatch_hook(event_arg, tool_name))


if __name__ == "__main__":
    main()
