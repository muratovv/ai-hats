"""One fact, one event: the facts an ``events/v1`` journal records more than once.

A fact with two producers lands twice — a fork replaying its parent's call, a
quota wall said by the stream and by the record. Each check below names a fact
by its identity; a journal that holds one identity twice has two producers.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

Event = Mapping[str, Any]

#: The fields that name one fact, per event kind; the first must be present.
IDENTITY: dict[str, tuple[str, ...]] = {
    "response_started": ("response_id",),
    "response_ended": ("response_id",),
    "tool_result_received": ("call_id",),
    "person_asked": ("call_id",),
    "prompt_received": ("prompt_id",),
    "gate_verdict": ("call_id", "point", "hook"),
}


def duplicate_facts(events: Iterable[Event]) -> list[str]:
    """Each fact recorded more than once, as ``"<kind> <identity> xN"``."""
    events = list(events)
    counts: Counter[tuple[Any, ...]] = Counter()
    turn = 0  # a main-agent prompt or turn end opens the next window for a wall
    for event in events:
        kind = str(event.get("event"))
        main = event.get("agent") is None
        fields = IDENTITY.get(kind)
        if fields is not None and event.get(fields[0]) is not None:
            counts[(kind, *(event.get(f) for f in fields))] += 1
        elif kind in ("run_started", "run_ended") and main:
            counts[(kind,)] += 1
        elif kind == "turn_ended" and main:
            for prompt_id in event.get("prompt_ids") or ():
                counts[(kind, prompt_id)] += 1
        elif _is_wall(event) and main:
            # a sub-agent's run has no turns of its own: two walls in it are two requests
            counts[("wait", "turn", turn)] += 1
        if main and kind in ("prompt_received", "turn_ended"):
            turn += 1
    found = [f"{key[0]} {key[1:]} x{n}" for key, n in counts.items() if n > 1]
    ends = [
        i for i, e in enumerate(events) if e.get("event") == "run_ended" and e.get("agent") is None
    ]
    if ends and ends[-1] != len(events) - 1:
        found.append(f"run_ended is not the last line ({len(events) - 1 - ends[-1]} after it)")
    return found


def _is_wall(event: Event) -> bool:
    return (
        event.get("event") == "signal"
        and event.get("obligation") == "harness_must_act"
        and event.get("kind") == "wait"
    )


def journal_duplicates(path: Path) -> list[str]:
    """``duplicate_facts`` over one ``events.jsonl``; a torn last line is not read."""
    text = path.read_text(encoding="utf-8")
    complete = text[: text.rfind("\n") + 1]
    return duplicate_facts(json.loads(line) for line in complete.split("\n") if line.strip())
