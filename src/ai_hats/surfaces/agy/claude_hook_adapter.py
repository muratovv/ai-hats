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

from ..hook_channel import matches, native_arg_keys, speak_args
from .profile import PROFILE


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
    """Whether a row's matcher applies to ``agy_tool``.

    A row declares the tool it guards in the matcher vocabulary — that is what
    every skill in the library ships — and the surface is the one that knows its
    own names. Asking the row's author to enumerate them is how exactly one row
    in eight came to name `run_command` while the rest silently guarded nothing.
    """
    return matches(PROFILE, matcher, agy_tool)


def to_claude_payload(payload: dict, event: str = "") -> dict:
    """One agy event as the Claude-shaped payload the hook scripts consume.

    ``event`` is written in because agy sends it on argv, not in the payload,
    and a hook reading no ``hook_event_name`` concludes the caller does not
    speak the protocol: ``pre_bash_shared_state_guard.sh`` answers that with a
    hard deny instead of the question it would otherwise put.
    """
    if not isinstance(payload, dict):
        return {}
    adapted = dict(payload)
    if event and not adapted.get("hook_event_name"):
        adapted["hook_event_name"] = event
    adapted["tool_input"] = speak_args(PROFILE, _args_of(payload))
    tool = agy_tool_name(payload)
    if tool:
        adapted["tool_name"] = PROFILE.spoken_name(tool)
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


def _spoken_keys(payload: dict) -> dict:
    """Vocabulary key → the key THIS payload used, for the keys it carried."""
    return native_arg_keys(PROFILE, _args_of(payload))


def _args_of(payload: dict) -> dict:
    """The call's arguments, under whichever of the two keys carried them."""
    if not isinstance(payload, dict):
        return {}
    args = payload.get("tool_input")
    if not isinstance(args, dict) or not args:
        call = payload.get("toolCall")
        args = call.get("args") if isinstance(call, dict) else None
    return args if isinstance(args, dict) else {}


__all__ = ["agy_tool_name", "from_claude_decision", "matches_claude_hook", "to_claude_payload"]
