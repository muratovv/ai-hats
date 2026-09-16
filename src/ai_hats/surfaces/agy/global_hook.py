"""Global hook dispatcher wiring for AGY surface."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ai_hats.materialization import MaterializationEntry, describe_merge_json

MANAGED_DISPATCHER_TAG = "ai-hats:global-dispatcher"
#: Registered user-globally, so it fires outside ai-hats sessions too — where
#: nothing was composed and there is no gate to miss. Inside one, an interpreter
#: it cannot run is a gate it cannot DELIVER, and the two were the same silent
#: exit 0 until now; codex's equivalent guard has always refused and said why.
DISPATCHER_COMMAND = (
    'sh -c \'if [ -z "$AI_HATS_SESSION_ID" ]; then exit 0; fi; '
    'if [ ! -x "$AI_HATS_PYTHON" ]; then printf "%s\\n" '
    "\"ai-hats-hook-dispatcher: incomplete dispatcher environment — this session'\"'\"'s "
    'hooks cannot run; restart it" >&2; exit 2; fi; '
    'exec "$AI_HATS_PYTHON" -m ai_hats.surfaces.agy.hook_dispatcher "$@"\' sh'
)
DISPATCHER_EVENTS = ("PreToolUse", "PostToolUse", "Stop", "Notification", "PostInvocation")


def desired_entry() -> dict:
    """The one managed row every event carries — identical in every venv, so
    two ai-hats installs never rewrite each other's."""
    return {
        "matcher": "*",
        "command": DISPATCHER_COMMAND,
        "_ai_hats_managed": MANAGED_DISPATCHER_TAG,
    }


def desired_hooks() -> dict:
    """What ai-hats adds to the person's settings document, and nothing else."""
    return {"hooks": {event: [desired_entry()] for event in DISPATCHER_EVENTS}}


def plan_global_hook(settings_path: Path) -> MaterializationEntry:
    """The registration as one entry outside the session root (ADR-0036 D2):
    application merges the managed row into whatever the person's file holds."""
    return dataclasses.replace(describe_merge_json(settings_path, desired_hooks()), escape=True)
