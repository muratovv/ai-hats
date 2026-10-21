#!/usr/bin/env python3
"""Deny the harness task list. The session tracker is rack.

The matcher is the tool name, so a harness without these tools never starts
the script. A payload that names some other tool is allowed: a mis-wired
matcher must not refuse Bash. Kill switch: AI_HATS_HARNESS_TASKS_OFF=1,
exported by the supervisor. Stdlib-only. Zero egress.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass, journal_catch
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False

    def journal_catch(rule: str, verdict: str, **_kw) -> bool:
        print(
            f"[catch-journal] NOT RECORDED ({rule}: {verdict}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_HOOK = "harness_task_gate.py"
_KILL_SWITCH = "AI_HATS_HARNESS_TASKS_OFF"
_TOOLS = frozenset(
    {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop", "TaskOutput"}
)

DENY_REASON = (
    "GUARDRAIL (safety-guard): blocked — {tool} is the harness task list. "
    "This session's tracker is rack. Ignore the reminder and do not narrate "
    "the choice. Next step: `rack ls`, `rack context <ID>`, or "
    '`rack transition <ID> <state> --log "..."`. '
    f"The supervisor exports {_KILL_SWITCH}=1 for a session that has no backlog "
    "CLI. There is no per-call override, because a guard the agent switches "
    "off is not a guard."
)


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook=_HOOK)
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook=_HOOK)
        return 0

    tool = str(payload.get("tool_name") or "")
    if tool not in _TOOLS:
        return 0

    journal_catch("harness_task_gate", "deny", hook=_HOOK, cmd=tool)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_REASON.format(tool=tool),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
