#!/usr/bin/env python3
"""HATS-1278 — deny Claude Code's worktree tool, naming the ai-hats flow instead.

Claude-surface only — the tool exists in no other harness, so this gate is inert
there and the skill stays surface-agnostic. ai-hats already owns worktree
lifecycle (state, locks, venv, task/<id> branch, drift guard); EnterWorktree
either builds a rival outside all of it or raises an unsuppressible approval
prompt. A bare permissions.deny would hide the tool with no explanation, so this
denies WITH the recipe. Kill switch: AI_HATS_WT_ENTRY_OFF=1. Fails open.
"""

from __future__ import annotations

import json
import sys
import os

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# The hooks are stdlib-only, so the journal arrives as a flattened sibling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_KILL_SWITCH = "AI_HATS_WT_ENTRY_OFF"

_CREATE_MSG = (
    "Stopped: EnterWorktree would create a worktree outside ai-hats — no state file, "
    "no lock registry, no per-worktree venv, and a branch name that breaks the "
    "card<->branch link. ai-hats already creates one for you: a task transitioned to "
    "`execute` prints its worktree path. Otherwise run `ai-hats wt create <type>/<name>`. "
    "Enter it with `cd <worktree-path>` — plain cd, no tool call."
)

_ENTER_MSG = (
    "Stopped: the worktree already exists — just `cd {path}`. EnterWorktree would "
    "relocate the session's permission root and raise an approval prompt for any path "
    "outside .claude/worktrees/, which cannot be suppressed or remembered. `cd` needs "
    "no approval and keeps every ai-hats hook wired."
)


def reason_for(tool_input: dict) -> str:
    path = tool_input.get("path")
    if path:
        return _ENTER_MSG.format(path=path)
    return _CREATE_MSG


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook="wt_entry_gate.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="wt_entry_gate.py")
        return 0

    # One dialect reaches this script: the surface's own bridge translates
    # before spawning it (`ai_hats_agy.claude_hook_adapter`, HATS-1776). A
    # second reading here would be a second truth about the same payload.
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason_for(tool_input),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
