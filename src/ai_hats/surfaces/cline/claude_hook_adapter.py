"""Translate native Cline tool events into ai-hats' Claude hook dialect."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..hook_channel import matches
from .profile import PROFILE

_PATCH_PATH = re.compile(r"^\*\*\* (Update|Add|Delete) File: (.+)$", re.MULTILINE)
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def matches_claude_hook(matcher: str, tool_name: str) -> bool:
    """Whether a composed row's ``matcher`` applies to this Cline tool call."""
    return matches(PROFILE, matcher, tool_name)


def _patch_targets(patch: str, cwd: str) -> list[tuple[str, Path]]:
    targets = [(kind, raw.strip()) for kind, raw in _PATCH_PATH.findall(patch)]
    targets.extend(("Update", raw.strip()) for raw in _PATCH_MOVE.findall(patch))
    base = Path(cwd or os.getcwd())
    seen: set[Path] = set()
    result: list[tuple[str, Path]] = []
    for kind, raw in targets:
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else base / path).resolve()
        if path not in seen:
            seen.add(path)
            result.append((kind, path))
    return result


def to_claude_hook_payloads(payload: dict, event: str) -> list[dict]:
    """Return Claude-compatible payloads for one native Cline event."""
    event_payload = payload.get(event[0].lower() + event[1:])
    event_payload = event_payload if isinstance(event_payload, dict) else {}
    tool_name = str(event_payload.get("toolName", event_payload.get("tool", "")))
    parameters = event_payload.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    cwd = ""
    roots = payload.get("workspaceRoots")
    if isinstance(roots, list) and roots:
        cwd = str(roots[0])

    def adapted(tool: str, tool_input: dict) -> dict:
        result = {
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": cwd,
        }
        if event == "PostToolUse" and "result" in event_payload:
            result["tool_response"] = event_payload["result"]
        return result

    if tool_name in {"bash", "run_commands"}:
        commands = parameters.get("commands")
        if not isinstance(commands, list):
            command = parameters.get("command")
            commands = [command] if isinstance(command, str) else []
        spread = [adapted("Bash", {"command": str(c)}) for c in commands if isinstance(c, str)]
        # Falling through on an empty spread rather than returning it: no payload
        # means the dispatcher's loop never runs, and a terminal call reaches the
        # agent with its terminal gates never consulted.
        if spread:
            return spread

    if tool_name == "apply_patch":
        patch = parameters.get("patch", parameters.get("command", ""))
        targets = _patch_targets(str(patch), cwd)
        if targets:
            many = len(targets) > 1
            return [
                adapted(
                    "MultiEdit" if many else ("Write" if kind == "Add" else "Edit"),
                    {
                        **parameters,
                        "file_path": str(path),
                        "path": str(path),
                    },
                )
                for kind, path in targets
            ]

    return [adapted(PROFILE.spoken_name(tool_name), parameters)]


__all__ = ["matches_claude_hook", "to_claude_hook_payloads"]
