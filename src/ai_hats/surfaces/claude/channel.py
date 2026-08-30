"""claude's four answers on the shared hook channel.

Everything between reading stdin and writing the reply is
:func:`ai_hats.surfaces.hook_dispatch.dispatch`; this package holds only what
claude alone can answer. A test refuses a call to the chain's primitives from
here, so reaching past `dispatch` cannot happen quietly.

Why claude runs its gates through ai-hats when the harness already runs them:
the harness runs them fail-open by contract. A gate whose script has vanished
lets the call through with `rc=0` and zero bytes on stderr, and one killed by
its own timeout does the same (measured, `poc-hook-delivery.md` M4 and M7).
Neither is reachable from `settings.json`, so owning the execution is the only
way an undelivered gate becomes a refusal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping, Sequence

from ai_hats.env import ENV_SESSION_CACHE_DIR
from ai_hats_observe.trace import ENV_SESSION_ID

from ..hook_channel import (
    ChainDecision,
    ChainVerdict,
    HookCall,
    HookEvent,
    HookRow,
    worded,
)
from ..hook_dispatch import Arrival, ManifestUnresolved, dispatch, manifest_rows
from .profile import PROFILE


#: What a `settings.json` entry runs. An entry that cannot start the dispatcher
#: must REFUSE (exit 2) and name the hatch — python honours the hatch everywhere
#: else, and this branch is precisely the one that never reaches python.
#: The tag that marks the entry as ours, per event.
DISPATCHER_TAG = "ai-hats:claude-dispatcher"

DISPATCHER_COMMAND = (
    'sh -c \'if [ -n "$AI_HATS_SESSION_ID" ] '
    '&& [ -n "$AI_HATS_SESSION_CACHE_DIR" ] && [ -x "$AI_HATS_PYTHON" ]; '
    'then exec "$AI_HATS_PYTHON" -m ai_hats.surfaces.claude.channel; '
    'elif [ -n "$AI_HATS_GATE_BROKEN_ACK" ]; '
    'then printf "%s\\n" "ai-hats-claude-hook: AI_HATS_GATE_BROKEN_ACK set, '
    'SKIPPED: incomplete dispatcher environment" >&2; '
    'else printf "%s\\n" "ai-hats-claude-hook: incomplete dispatcher environment '
    '- set AI_HATS_GATE_BROKEN_ACK=1 to run past it" >&2; '
    "exit 2; fi'"
)


class ClaudeChannel:
    """The five things only claude can answer."""

    profile = PROFILE

    def arrival(self, payload: dict, argv: Sequence[str]) -> Arrival:
        """From the payload alone: the harness names the event in it, and passes
        no argv the dispatcher could read it from."""
        return Arrival.of(str(payload.get("hook_event_name", "")))

    def rows(self, environ: Mapping[str, str], event: HookEvent) -> list[HookRow]:
        session_id = environ.get(ENV_SESSION_ID, "")
        cache_dir = environ.get(ENV_SESSION_CACHE_DIR, "")
        if not session_id or not cache_dir:
            raise ManifestUnresolved(
                f"incomplete hook identity: {ENV_SESSION_ID} and {ENV_SESSION_CACHE_DIR} "
                f"are required — this session predates the dispatcher; restart it"
            )
        cache = Path(cache_dir)
        skills_root = PROFILE.skills_root(cache)
        if skills_root is None:
            raise ManifestUnresolved(f"{PROFILE.label} declares no session skills mirror")
        return manifest_rows(
            PROFILE.manifest_path(cache),
            event=event,
            session_id=session_id,
            skills_root=skills_root.expanduser().resolve(),
        )

    def read(self, payload: dict, arrival: Arrival) -> list[HookCall]:
        """One call, untranslated: the payload is already in the vocabulary the
        hooks are written against."""
        return [HookCall(payload, str(payload.get("tool_name", "")))]

    def emit(self, verdict: ChainVerdict, arrival: Arrival) -> None:
        """Write the dialect `parse_reply` already reads.

        The document carries the refusal, not the status: exit 2 blocks too, but
        its reason travels on stderr alone, which drops the ticket and the
        advice this dialect can hold. `imposed_status` stays 0 for that reason.
        """
        if verdict.decision is ChainDecision.DENY and arrival.event is HookEvent.POST_TOOL_USE:
            # PostToolUse has no `permissionDecision`; this pair is how a refusal
            # is spelled for a call that already ran.
            _write({"decision": "block", "reason": worded(verdict)})
            _also_on_stderr(verdict)
            return
        spoken: dict[str, object] = {"hookEventName": arrival.native}
        if verdict.decision is not ChainDecision.ALLOW:
            spoken["permissionDecision"] = verdict.decision.value
            spoken["permissionDecisionReason"] = worded(verdict)
        if verdict.updated_input is not None:
            spoken["updatedInput"] = verdict.updated_input
        # Carried on every path: the dialect says this surface holds advice, and
        # dropping it on a refusal would make that false for the gates that ran
        # before the objector.
        if verdict.nudges:
            spoken["additionalContext"] = "\n".join(n.text for n in verdict.nudges)
        if len(spoken) > 1:
            _write({"hookSpecificOutput": spoken})
        _also_on_stderr(verdict)


def _write(document: dict) -> None:
    sys.stdout.write(json.dumps(document) + "\n")


def _also_on_stderr(verdict: ChainVerdict) -> None:
    """The model reads the JSON; an operator reading the session log reads this.

    Only a refusal: an allow has nothing a human needs to be told twice.
    """
    if verdict.decision is ChainDecision.DENY:
        sys.stderr.write(worded(verdict) + "\n")


def main() -> None:
    raise SystemExit(dispatch(ClaudeChannel()))


if __name__ == "__main__":
    main()


__all__ = ["DISPATCHER_COMMAND", "ClaudeChannel", "main"]
