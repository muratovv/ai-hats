"""Translate native grok hook payloads into ai-hats' Claude hook dialect.

Grok sends camelCase (``toolName``, ``toolInput``) and Claude's snake_case
aliases of the same fields. The payload a gate reads carries the matcher
vocabulary name; the call keeps the native name beside it so a ``Bash``
matcher still reaches ``run_terminal_command``.
"""

from __future__ import annotations

import ast
import json

from ..hook_channel import HookCall
from .profile import PROFILE

_NAME_KEYS = ("tool_name", "toolName")
_INPUT_KEYS = ("tool_input", "toolInput")
_RESULT_KEYS = ("tool_response", "toolResult", "tool_output")


def _coerce_tool_input(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(raw)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return {"_raw": raw}
    return {}


def _first(payload: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def payload_drift_notes(payload: dict) -> list[str]:
    """Shape changes the adapter tolerates, said so a silent allow is visible.

    A call with no tool name runs every gate. A tool input that does not parse
    hands those gates no arguments, and most then allow.
    """
    notes = []
    if _first(payload, _NAME_KEYS) is None:
        notes.append("no tool_name — matchers cannot filter; grok may have renamed the field")
    if not any(key in payload for key in _INPUT_KEYS):
        notes.append("no tool_input — gates see no arguments")
    else:
        raw = _first(payload, _INPUT_KEYS)
        if isinstance(raw, str) and raw.strip() and "_raw" in _coerce_tool_input(raw):
            notes.append("tool_input is a string that parses to no object — gates see no arguments")
    return notes


def to_claude_hook_calls(payload: dict, event: str) -> list[HookCall]:
    """One call. The payload spells the matcher name; the call keeps the native."""
    native = str(_first(payload, _NAME_KEYS) or "")
    adapted = {
        "hook_event_name": event,
        "tool_name": PROFILE.spoken_name(native),
        "tool_input": _coerce_tool_input(_first(payload, _INPUT_KEYS)),
        "cwd": str(payload.get("cwd", "")),
    }
    if event == "PostToolUse":
        result = _first(payload, _RESULT_KEYS)
        if result is not None:
            adapted["tool_response"] = result
    return [HookCall(adapted, native)]


__all__ = ["payload_drift_notes", "to_claude_hook_calls"]
