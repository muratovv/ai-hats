"""Translate native agy tool events into ai-hats' Claude hook dialect.

The composed runtime-hook library predates provider surfaces and its scripts
consume Claude-shaped ``PreToolUse`` payloads. Keep that compatibility bridge
explicit and surface-local: the dispatcher owns orchestration, while this module
owns every agy-to-Claude tool name and payload conversion — the same division
``ai_hats.surfaces.codex.claude_hook_adapter`` already draws for Codex.

Before this the bridge existed nowhere and half-existed everywhere: the tool
name was translated by one hard-coded branch in the provider, the payload by
four hand-rolled fallbacks inside the hook scripts themselves, and the two
shell guards not at all — so on agy they read an empty command and allowed it.

One direction more than Codex needs: a verdict may REWRITE the tool's input
(the consent ticket, HATS-1642), and the rewrite has to leave in the key the
surface spoke in.
"""  # comment-length: allow — which side owns the bridge is the contract

from __future__ import annotations

import re

#: agy's terminal tools ↔ Claude's. `execute` rides along because a shipped row
#: already enumerated it by hand (`safety-guard`'s `Bash|run_command|execute`).
_TERMINAL = ("run_command", "execute")
_CLAUDE_TERMINAL = ("Bash",)

#: agy's file-mutating tools ↔ Claude's. This IS the table that used to live in
#: the provider as ``AGY_FILE_MUTATION_MATCHER`` — one class, not four shades:
#: every shipped row treats the whole class alike.
_FILE_MUTATION = ("Create", "write_to_file", "replace_file_content", "multi_replace_file_content")
_CLAUDE_FILE_MUTATION = ("Edit", "Write", "MultiEdit")

#: agy's argument names ↔ Claude's. Measured from what the hooks defend against
#: today: `backlog_write_gate` and `wt_gate` each fan out over five spellings of
#: the same path, and `safety_gate` over two spellings of the same command.
_ARG_KEYS = {
    "CommandLine": "command",
    "TargetFile": "file_path",
    "AbsolutePath": "file_path",
    "target_file": "file_path",
}


def agy_tool_name(payload: dict) -> str:
    """The tool this call is about, from the PAYLOAD.

    Never from argv: agy may or may not pass the name there, and the dispatcher's
    matcher filter used to switch itself off when it did not — so a Bash guard
    saw file edits and an edit gate saw shell commands. The payload always
    carries it, in one of two spellings.
    """
    if not isinstance(payload, dict):
        return ""
    named = payload.get("tool_name")
    if isinstance(named, str) and named:
        return named
    call = payload.get("toolCall")
    if isinstance(call, dict):
        inner = call.get("name")
        if isinstance(inner, str):
            return inner
    return ""


def matches_claude_hook(matcher: str, agy_tool: str) -> bool:
    """Whether a row's Claude-vocabulary matcher applies to ``agy_tool``.

    A row declares the tool it guards in Claude's words — that is what every
    skill in the library ships — and the surface is the one that knows its own
    names. Asking the row's author to enumerate them is how exactly one row in
    eight came to name `run_command` while the rest silently guarded nothing.
    """
    if not matcher or matcher == "*":
        return True
    aliases = [agy_tool]
    if agy_tool in _TERMINAL:
        aliases.extend(_CLAUDE_TERMINAL)
    elif agy_tool in _FILE_MUTATION:
        aliases.extend(_CLAUDE_FILE_MUTATION)
    try:
        return any(re.fullmatch(matcher, candidate) is not None for candidate in aliases)
    except re.error:
        return any(candidate in matcher.split("|") for candidate in aliases)


def to_claude_payload(payload: dict) -> dict:
    """One agy event as the Claude-shaped payload the hook scripts consume."""
    if not isinstance(payload, dict):
        return {}
    adapted = dict(payload)
    args = payload.get("tool_input")
    if not isinstance(args, dict) or not args:
        call = payload.get("toolCall")
        args = call.get("args") if isinstance(call, dict) else None
    adapted["tool_input"] = _claude_args(args if isinstance(args, dict) else {})
    tool = agy_tool_name(payload)
    if tool:
        adapted["tool_name"] = _claude_tool(tool)
    return adapted


def from_claude_decision(decision: dict, payload: dict) -> dict:
    """A hook's Claude-shaped verdict, answered in the dialect agy speaks.

    Only the rewritten input needs turning back: the verdict envelope itself is
    what agy already reads. A ticket written back under `command` reaches a
    surface that looks for `CommandLine`, finds none, and runs the original line
    — a consent gate that asks and is then ignored.
    """
    if not isinstance(decision, dict):
        return decision
    output = decision.get("hookSpecificOutput")
    if not isinstance(output, dict):
        return decision
    updated = output.get("updatedInput")
    if not isinstance(updated, dict):
        return decision
    spoken = _spoken_keys(payload)
    if not spoken:
        return decision
    return {
        **decision,
        "hookSpecificOutput": {
            **output,
            "updatedInput": {spoken.get(key, key): value for key, value in updated.items()},
        },
    }


def _claude_tool(agy_tool: str) -> str:
    if agy_tool in _TERMINAL:
        return _CLAUDE_TERMINAL[0]
    if agy_tool in _FILE_MUTATION:
        return _CLAUDE_FILE_MUTATION[0]
    return agy_tool


def _claude_args(args: dict) -> dict:
    """Rename what has a Claude name; never overwrite a Claude key already there."""
    renamed = dict(args)
    for spoken, canonical in _ARG_KEYS.items():
        if spoken in renamed and canonical not in renamed:
            renamed[canonical] = renamed.pop(spoken)
    return renamed


def _spoken_keys(payload: dict) -> dict:
    """Claude key → the key THIS payload used, for the keys it actually carried."""
    if not isinstance(payload, dict):
        return {}
    args = payload.get("tool_input")
    if not isinstance(args, dict) or not args:
        call = payload.get("toolCall")
        args = call.get("args") if isinstance(call, dict) else None
    if not isinstance(args, dict):
        return {}
    return {_ARG_KEYS[key]: key for key in args if key in _ARG_KEYS}


__all__ = ["agy_tool_name", "from_claude_decision", "matches_claude_hook", "to_claude_payload"]
