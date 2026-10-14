"""Translate native kimi hook payloads into ai-hats' Claude hook dialect."""

from __future__ import annotations

import ast
import json

from ..hook_channel import HookCall, matches
from .profile import PROFILE


def matches_claude_hook(matcher: str, tool_name: str) -> bool:
    """Whether a composed row's ``matcher`` applies to this kimi tool call."""
    return matches(PROFILE, matcher, tool_name)


def _coerce_tool_input(raw: object) -> dict:
    """Kimi ships ``tool_input`` as a STRING of a dict repr, not nested JSON."""
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


def payload_drift_notes(payload: dict) -> list[str]:
    """Shape changes the adapter tolerates, as one-line notes.

    Tolerated must not mean invisible: a call with no readable ``tool_name``
    runs EVERY gate (the fail-safe direction), and a ``tool_input`` the adapter
    cannot parse hands those gates no arguments — most then allow. The note is
    the only trace that the kimi envelope drifted."""
    notes = []
    if not payload.get("tool_name"):
        notes.append("no tool_name — matchers cannot filter; kimi may have renamed the field")
    if "tool_input" not in payload:
        notes.append("no tool_input — gates see no arguments")
    else:
        raw = payload["tool_input"]
        if isinstance(raw, str) and raw.strip() and "_raw" in _coerce_tool_input(raw):
            notes.append("tool_input is a string that parses to no object — gates see no arguments")
    return notes


def to_claude_hook_calls(payload: dict, event: str) -> list[HookCall]:
    """One call, already Claude-shaped; only the envelope fields are re-keyed."""
    tool = str(payload.get("tool_name", ""))
    adapted = {
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": _coerce_tool_input(payload.get("tool_input")),
        "cwd": str(payload.get("cwd", "")),
    }
    if event == "PostToolUse" and "tool_output" in payload:
        adapted["tool_response"] = payload["tool_output"]
    return [HookCall(adapted, tool)]


__all__ = ["matches_claude_hook", "payload_drift_notes", "to_claude_hook_calls"]
