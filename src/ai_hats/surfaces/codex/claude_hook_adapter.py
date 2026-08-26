"""Translate native Codex tool events into ai-hats' Claude hook dialect.

The composed runtime-hook library predates provider surfaces and its scripts
consume Claude-shaped ``PreToolUse`` payloads.  Keep that compatibility bridge
explicit and surface-local: the dispatcher owns orchestration, while this
module owns every Codex-to-Claude tool name and payload conversion.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PATCH_PATH = re.compile(r"^\*\*\* (Update|Add|Delete) File: (.+)$", re.MULTILINE)
_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def matches_claude_hook(matcher: str, codex_tool_name: str) -> bool:
    """Match a Claude hook matcher against the equivalent Codex tool name."""
    aliases = [codex_tool_name]
    if codex_tool_name == "apply_patch":
        aliases.extend(("Edit", "Write", "MultiEdit"))
    elif codex_tool_name == "spawn_agent":
        aliases.append("Agent")
    if not matcher or matcher == "*":
        return True
    try:
        return any(re.fullmatch(matcher, candidate) is not None for candidate in aliases)
    except re.error:
        return matcher in aliases


def _patch_targets(command: str, cwd: str) -> list[tuple[str, Path]]:
    targets = [(kind, raw.strip()) for kind, raw in _PATCH_PATH.findall(command)]
    targets.extend(("Update", raw.strip()) for raw in _PATCH_MOVE.findall(command))
    seen: set[Path] = set()
    resolved: list[tuple[str, Path]] = []
    base = Path(cwd or os.getcwd())
    for kind, raw in targets:
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else base / path
        path = path.resolve()
        if path not in seen:
            seen.add(path)
            resolved.append((kind, path))
    return resolved


def to_claude_hook_payloads(payload: dict, event: str) -> list[dict]:
    """Return one or more Claude-compatible payloads for one Codex event."""
    adapted = dict(payload)
    # Existing ai-hats hooks speak the Claude PreToolUse dialect. Re-running
    # them during a Codex PermissionRequest must not make them fall back to the
    # provider-agnostic exit-2 dialect merely because the event name differs.
    if event == "PermissionRequest":
        adapted["hook_event_name"] = "PreToolUse"
    if str(payload.get("tool_name", "")) != "apply_patch":
        return [adapted]
    tool_input = payload.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    targets = _patch_targets(str(command), str(payload.get("cwd", "")))
    if not targets:
        return [adapted]
    result: list[dict] = []
    for kind, path in targets:
        one = dict(adapted)
        many = len(targets) > 1
        one["tool_name"] = "MultiEdit" if many else ("Write" if kind == "Add" else "Edit")
        one["tool_input"] = {
            **(tool_input if isinstance(tool_input, dict) else {}),
            "file_path": str(path),
            "path": str(path),
        }
        result.append(one)
    return result


__all__ = ["matches_claude_hook", "to_claude_hook_payloads"]
