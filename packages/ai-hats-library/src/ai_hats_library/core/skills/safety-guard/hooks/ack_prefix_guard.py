#!/usr/bin/env python3
"""HATS-1944 — refuse an approval the agent writes for itself on the command line.

Flags held by a PreToolUse guard are read from the LAUNCHING environment, so a
prefix never reaches them. Flags read by a script inside ``make`` or a git hook do
get reached: ``AI_HATS_RED_MASTER_ACK=1 make done-gate`` lands in
``check_master_ci.py`` by plain shell semantics. That asymmetry was never a policy,
only which side of a process boundary the flag was read on; it is closed here, where
every flag crosses. Shape not roster (mirrors ``constants.withheld_from_subagent``);
no hatch, since one would be the same self-grant a level up.
"""

from __future__ import annotations

import json
import os
import sys

# Hooks are stdlib-only and run in place; helpers are siblings in this directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from shell_walk import _tokens, leading_assignments, segments
except ImportError:  # the parser is this guard's eyes — it must not guess without them
    segments = None  # type: ignore[assignment]

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


#: Mirrors ``ai_hats.constants.BYPASS_FLAG_SUFFIXES``. Pinned by a contract test.
BYPASS_FLAG_SUFFIXES = ("_ACK", "_OFF", "_SKIP")

#: Mirrors ``ai_hats.constants.BYPASS_FLAGS_OFF_CONVENTION``. Pinned by a contract test.
BYPASS_FLAGS_OFF_CONVENTION = frozenset({"AI_HATS_SKIP_SELF_LOCATION_GUARD", "AI_HATS_YOLO"})

#: Consent's own artefacts, placed by the consent engine and gated by
#: ``safety_gate.py``. Excluded here for the reason safety-guard states about
#: ``git push``: two gates on one concern means the coarser one silently wins.
CONSENT_OWNED_KEYS = frozenset(
    {
        "AI_HATS_CONSENT_ACK",
        "AI_HATS_CONSENT_TICKET",
        "AI_HATS_MERGE_ACK",
        "AI_HATS_PLAN_ACK",
    }
)


def is_self_grantable(name: str) -> bool:
    """Is ``name`` an approval that must come from the supervisor, not this line?"""
    if not name.startswith("AI_HATS_") or name in CONSENT_OWNED_KEYS:
        return False
    return name in BYPASS_FLAGS_OFF_CONVENTION or name.endswith(BYPASS_FLAG_SUFFIXES)


def granted_by(command: str) -> list[str] | None:
    """Flags ``command`` binds for itself; ``None`` when the line could not be read.

    Unreadable is not the same as clean, and the caller says so out loud: refusing on
    a guess and passing on one are both worse than a journalled fail-open."""
    try:
        tokens = _tokens(command)
    except ValueError:
        return None
    found: list[str] = []
    for segment in segments(tokens):
        found.extend(n for n in leading_assignments(segment) if is_self_grantable(n))
    return found


def _reason(flags: list[str]) -> str:
    named = ", ".join(dict.fromkeys(flags))
    return (
        f"self-granted approval refused (safety-guard): this command line sets {named}. "
        "A gate flag is the supervisor's word, and writing it yourself is not a way of "
        "having it — it is the one thing the flag exists to rule out. There is no flag "
        "that opens this guard; the way through is the supervisor setting it in the "
        "environment that LAUNCHES the agent, which a prefix here cannot reach.\n\n"
        "If a stage is red and your change did not cause it, the flag is not the next "
        "step and neither is fixing it blind. Establish attribution first: run that same "
        "stage on your base commit in the SAME venv, diff the failure LISTS rather than "
        "the counts, and name the card that owns each line. Then report the "
        "classification and let the supervisor decide. Skill `red-attribution` has the "
        "procedure and the traps it exists to avoid."
    )


def main() -> int:
    if segments is None:
        journal_bypass("fail-open", "shell_walk.py missing", hook="ack_prefix_guard.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="ack_prefix_guard.py")
        return 0

    tool_input = payload.get("tool_input")
    command = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or "AI_HATS_" not in command:
        return 0  # nothing of ours in the line -> no parse

    flags = granted_by(command)
    if flags is None:
        journal_bypass("fail-open", "unreadable command line", hook="ack_prefix_guard.py")
        return 0
    if not flags:
        return 0

    journal_catch("ack-prefix-guard", "deny", hook="ack_prefix_guard.py", cmd=command)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _reason(flags),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
