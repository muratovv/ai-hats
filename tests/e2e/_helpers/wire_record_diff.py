"""The main agent's events a headless log took off the wire, against its record read after.

One session, the reader's two inputs: ``events.jsonl`` holds what ``feed`` made of
the wire, the surface's transcript is read with ``read()`` once the session is
over. They must say the same up to the declared differences (ADR-0037, the
reader with two inputs); each is taken out below, and nothing else is.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from ai_hats_observe.event_log import encode
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

# The reader's own sources; the gate chain's verdicts are in no record.
_OURS = {None, "claude/wire", "claude/jsonl"}
_ENDS = {"run_started", "run_ended", "turn_ended"}


def from_log(events: Iterable[Mapping[str, Any]]) -> Counter:
    """The main agent's events in a headless log, comparable."""
    events = list(events)
    folded = {i for e in events if e.get("event") == "turn_ended" for i in e["prompt_ids"][1:]}
    kept = [
        e
        for e in events
        if not e.get("agent")
        and not (e.get("event") == "prompt_received" and e.get("prompt_id") in folded)
    ]
    return _comparable(kept)


def from_record(transcript: Path, *, wire_prompt_ids: set[str]) -> Counter:
    """The same session's record, read after the fact, comparable."""
    events = [encode(e) for e in ClaudeTranscriptReader(transcript).read()]
    kept = [
        e
        for e in events
        if not (
            e.get("event") == "prompt_received"
            and e.get("prompt_id") not in wire_prompt_ids
            and str(e.get("text", "")).startswith("<task-notification>")
        )
        and not (
            e.get("kind") == "unsupported_record"
            and str(e.get("raw_code", "")).startswith("attachment/")
        )
    ]
    return _comparable(kept)


def _comparable(events: Iterable[Mapping[str, Any]]) -> Counter:
    kept = []
    for event in events:
        if event.get("event") in _ENDS or event.get("source") not in _OURS:
            continue
        if event.get("event") == "gate_verdict" and event.get("point") == "at_stop":
            continue  # one per hook on the wire, one per stop in the record
        record = {k: v for k, v in event.items() if k != "ts"}
        if record.get("source") == "claude/wire":
            record["source"] = "claude/jsonl"
        if record.get("kind") == "surface_warning":
            record["raw_code"] = record["detail"] = "<hook failure>"
        if record.get("obligation") in ("harness_must_act", "person_must_act"):
            record["raw_code"] = "<api error code>"
        kept.append(json.dumps(record, sort_keys=True))
    return Counter(kept)


def assert_same(log: Counter, record: Counter) -> None:
    kinds = {json.loads(line)["event"] for line in log}
    assert {"item_emitted", "response_ended"} <= kinds, f"nothing to compare: {kinds}"
    assert log == record, {"log only": log - record, "record only": record - log}


__all__ = ["assert_same", "from_log", "from_record"]
