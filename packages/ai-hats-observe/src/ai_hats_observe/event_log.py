"""The structured session artifact: canonical events, one JSON object per line.

HATS-1966 S5. ``audit.md`` keeps what a person's rendering needs; this keeps the
events, so the audit becomes one projection of the record rather than the record
itself — judge, A/B comparison and cost report read it through ``canonical.views``.

Append-friendly, because HATS-1967 will write it live: no header, no trailing
marker, one whole event per line, each flushed as written. A half-written final
line carries no newline and the reader stops at the last one, so a torn tail
costs only the event that had not finished arriving. Pure in the stream and the
path — nothing here knows about sessions or directories.
"""  # comment-length: allow — the on-disk format IS this module's contract

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from .artifacts import EVENT_LOG_JSONL, private_opener
from .canonical.events import (
    Event,
    GateVerdict,
    ItemDelta,
    ItemEmitted,
    PersonAsked,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    RunEnded,
    RunStarted,
    ToolResultReceived,
)
from .canonical.signals import (
    HarnessActionRequired,
    HarnessMustAct,
    Notice,
    PersonActionRequired,
    PersonMustAct,
    Signal,
    WorthRecording,
)
from .canonical.types import (
    AgentId,
    AskKind,
    Completion,
    EpochSeconds,
    GateDecision,
    GatePoint,
    Item,
    ItemKind,
    ModelName,
    PromptOrigin,
    ResponseId,
    TextItem,
    ThinkingItem,
    Timestamp,
    ToolCallId,
    ToolCallItem,
    Usage,
)

#: Stamped on every line, not on a header: a headerless file can be appended to
#: by a live writer and truncated by a crash without a reader losing the version.
EVENT_SCHEMA_VERSION = "events/v1"

# The three obligations, named as the type names them. A signal's obligation is
# carried by its class, so the wire form spells it out instead of leaving a
# reader to infer it from the reason.
_PERSON = "person_must_act"
_HARNESS = "harness_must_act"
_RECORDING = "worth_recording"


# --- signals ---------------------------------------------------------------


def signal_fields(signal: Signal) -> dict[str, Any]:
    """One signal as plain data — the shape both this artifact and the usage
    report carry, so run health reads the same in either file."""
    fields: dict[str, Any] = {
        "obligation": _obligation(signal),
        "kind": str(signal.reason),
        "ts": signal.ts,
        "detail": signal.detail,
        "raw_code": signal.raw_code,
        "source": signal.source,
    }
    match signal:
        case HarnessActionRequired():
            fields["retry_after"] = signal.retry_after
        case Notice():
            fields["model"] = signal.model
    return fields


def _obligation(signal: Signal) -> str:
    match signal:
        case PersonActionRequired():
            return _PERSON
        case HarnessActionRequired():
            return _HARNESS
        case _:
            return _RECORDING


def _signal_from(record: dict[str, Any]) -> Signal | None:
    shared = {
        "ts": _ts(record.get("ts")),
        "detail": record.get("detail"),
        "raw_code": record.get("raw_code"),
        "source": record.get("source"),
    }
    kind = record.get("kind")
    match record.get("obligation"):
        case "person_must_act" if kind in set(PersonMustAct):
            return PersonActionRequired(**shared, reason=PersonMustAct(kind))
        case "harness_must_act" if kind in set(HarnessMustAct):
            retry_after = record.get("retry_after")
            return HarnessActionRequired(
                **shared,
                reason=HarnessMustAct(kind),
                retry_after=EpochSeconds(retry_after) if isinstance(retry_after, int) else None,
            )
        case "worth_recording" if kind in set(WorthRecording):
            model = record.get("model")
            return Notice(
                **shared,
                reason=WorthRecording(kind),
                model=ModelName(model) if isinstance(model, str) and model else None,
            )
    return None


# --- items -----------------------------------------------------------------


def _item_fields(item: Item) -> dict[str, Any]:
    match item:
        case ThinkingItem():
            return {"kind": str(ItemKind.THINKING), "text": item.text, "redacted": item.redacted}
        case ToolCallItem():
            return {
                "kind": str(ItemKind.TOOL_CALL),
                "call_id": item.call_id,
                "name": item.name,
                "input": item.input,
            }
        case _:
            return {"kind": str(ItemKind.TEXT), "text": item.text}


def _item_from(record: Any) -> Item | None:
    if not isinstance(record, dict):
        return None
    text = str(record.get("text", ""))
    match record.get("kind"):
        case ItemKind.TEXT:
            return TextItem(text)
        case ItemKind.THINKING:
            return ThinkingItem(text, redacted=bool(record.get("redacted")))
        case ItemKind.TOOL_CALL:
            inputs = record.get("input")
            return ToolCallItem(
                call_id=ToolCallId(str(record.get("call_id", ""))),
                name=str(record.get("name", "")),
                input=inputs if isinstance(inputs, dict) else {},
            )
    return None


# --- events ----------------------------------------------------------------


def encode(event: Event) -> dict[str, Any]:
    """One event as the dict written to a line.

    Raises ``TypeError`` on an event this writer does not know: an unencodable
    event is a gap in the writer, and writing the session record with that event
    silently missing is the one failure this artifact exists to prevent.
    """
    match event:
        case RunStarted():
            body = {"event": "run_started", "ts": event.ts}
        case RunEnded():
            body = {
                "event": "run_ended",
                "ok": event.ok,
                "raw_code": event.raw_code,
                "detail": event.detail,
                "ts": event.ts,
            }
        case PromptReceived():
            body = {
                "event": "prompt_received",
                "text": event.text,
                "origin": None if event.origin is None else str(event.origin),
                "ts": event.ts,
            }
        case PersonAsked():
            body = {
                "event": "person_asked",
                "kind": str(event.kind),
                "call_id": event.call_id,
                "tool": event.tool,
                "detail": event.detail,
                "source": event.source,
                "ts": event.ts,
            }
        case ResponseStarted():
            body = {
                "event": "response_started",
                "response_id": event.response_id,
                "model": event.model,
                "ts": event.ts,
            }
        case ItemEmitted():
            body = {
                "event": "item_emitted",
                "response_id": event.response_id,
                "item": _item_fields(event.item),
                "ts": event.ts,
            }
        case ItemDelta():
            body = {
                "event": "item_delta",
                "response_id": event.response_id,
                "index": event.index,
                "text": event.text,
                "ts": event.ts,
            }
        case ToolResultReceived():
            body = {
                "event": "tool_result_received",
                "call_id": event.call_id,
                "ok": event.ok,
                "content": event.content,
                "ts": event.ts,
            }
        case ResponseEnded():
            body = {
                "event": "response_ended",
                "response_id": event.response_id,
                "completion": str(event.completion),
                "usage": _usage_fields(event.usage),
                "stop_reason": event.stop_reason,
                "ts": event.ts,
            }
        case GateVerdict():
            body = {
                "event": "gate_verdict",
                "point": str(event.point),
                "decision": str(event.decision),
                "hook": event.hook,
                "reason": event.reason,
                "nudges": [{"hook": hook, "text": text} for hook, text in event.nudges],
                "tool": event.tool,
                "call_id": event.call_id,
                "source": event.source,
                "ts": event.ts,
            }
        case PersonActionRequired() | HarnessActionRequired() | Notice():
            body = {"event": "signal", **signal_fields(event)}
        case _:
            raise TypeError(f"no encoding for {type(event).__name__}")
    if event.agent is not None:
        body["agent"] = event.agent
    return {"v": EVENT_SCHEMA_VERSION, **body}


def _gate_verdict_from(record: dict[str, Any]) -> GateVerdict | None:
    point, decision = record.get("point"), record.get("decision")
    if point not in set(GatePoint) or decision not in set(GateDecision):
        return None
    tool, call_id, source = record.get("tool"), record.get("call_id"), record.get("source")
    return GateVerdict(
        point=GatePoint(point),
        decision=GateDecision(decision),
        hook=str(record.get("hook") or ""),
        reason=str(record.get("reason") or ""),
        nudges=tuple(
            (str(nudge.get("hook") or ""), str(nudge.get("text") or ""))
            for nudge in record.get("nudges") or ()
            if isinstance(nudge, dict)
        ),
        tool=tool if isinstance(tool, str) else None,
        call_id=ToolCallId(call_id) if isinstance(call_id, str) else None,
        source=source if isinstance(source, str) else None,
        ts=_ts(record.get("ts")),
    )


def decode(record: dict[str, Any]) -> Event | None:
    """One line's dict back into an event; ``None`` when the line is not one.

    Never raises: this reads a file another process may be mid-way through
    writing, and a shape we do not recognise is a line to skip, not a read to
    abandon.
    """
    event = _decode(record)
    agent = record.get("agent")
    if event is None or not isinstance(agent, str) or not agent:
        return event
    return replace(event, agent=AgentId(agent))


def _decode(record: dict[str, Any]) -> Event | None:
    response_id = ResponseId(str(record.get("response_id", "")))
    ts = _ts(record.get("ts"))
    match record.get("event"):
        case "run_started":
            return RunStarted(ts=ts)
        case "run_ended":
            raw_code, detail = record.get("raw_code"), record.get("detail")
            return RunEnded(
                ok=bool(record.get("ok")),
                raw_code=raw_code if isinstance(raw_code, str) else None,
                detail=detail if isinstance(detail, str) else None,
                ts=ts,
            )
        case "prompt_received":
            origin = record.get("origin")
            return PromptReceived(
                text=str(record.get("text", "")),
                ts=ts,
                origin=PromptOrigin(origin) if origin in set(PromptOrigin) else None,
            )
        case "person_asked":
            kind = record.get("kind")
            if kind not in set(AskKind):
                return None
            call_id, tool = record.get("call_id"), record.get("tool")
            detail, source = record.get("detail"), record.get("source")
            return PersonAsked(
                kind=AskKind(kind),
                call_id=ToolCallId(call_id) if isinstance(call_id, str) else None,
                tool=tool if isinstance(tool, str) else None,
                detail=detail if isinstance(detail, str) else None,
                source=source if isinstance(source, str) else None,
                ts=ts,
            )
        case "response_started":
            model = record.get("model")
            return ResponseStarted(
                response_id=response_id,
                model=ModelName(model) if isinstance(model, str) and model else None,
                ts=ts,
            )
        case "item_emitted":
            item = _item_from(record.get("item"))
            return None if item is None else ItemEmitted(response_id, item, ts)
        case "item_delta":
            index = record.get("index")
            return ItemDelta(
                response_id=response_id,
                index=index if isinstance(index, int) else 0,
                text=str(record.get("text", "")),
                ts=ts,
            )
        case "tool_result_received":
            return ToolResultReceived(
                call_id=ToolCallId(str(record.get("call_id", ""))),
                ok=bool(record.get("ok")),
                content=record.get("content"),
                ts=ts,
            )
        case "response_ended":
            completion = record.get("completion")
            stop_reason = record.get("stop_reason")
            return ResponseEnded(
                response_id=response_id,
                completion=Completion(completion)
                if completion in set(Completion)
                else Completion.UNKNOWN,
                usage=_usage_from(record.get("usage")),
                stop_reason=stop_reason if isinstance(stop_reason, str) else None,
                ts=ts,
            )
        case "gate_verdict":
            return _gate_verdict_from(record)
        case "signal":
            return _signal_from(record)
    return None


# --- the file ---------------------------------------------------------------


def write_events(events: Iterable[Event], path: Path | str, *, append: bool = False) -> int:
    """Write ``events`` to ``path``, one JSON object per line; return the count.

    ``append`` continues an existing file — the mode the session's own writer
    uses. Every line is one ``write(2)`` on an ``O_APPEND`` descriptor, never a
    buffered handle: a hook process appends its verdict to the same file while
    the session's writer is appending, and neither can see the other, so a line
    delivered in one call is what keeps the two from interleaving. A reader
    following the file sees an event the moment that call returns. The file is
    private from the moment it exists.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | (0 if append else os.O_TRUNC)
    fd = private_opener(str(target), flags)
    written = 0
    try:
        for event in events:
            data = (json.dumps(encode(event), ensure_ascii=False, default=str) + "\n").encode(
                "utf-8"
            )
            if os.write(fd, data) != len(data):
                raise OSError(f"short write to {target}: event {written} is torn")
            written += 1
    finally:
        os.close(fd)
    return written


def append_event(event: Event, path: Path | str) -> None:
    """One event onto the end of ``path`` — what a producer outside the session's
    own writer calls, a hook process recording the verdict it just gave."""
    write_events((event,), path, append=True)


def read_events(path: Path | str) -> Iterator[Event]:
    """Yield every complete event in ``path``; missing file yields nothing.

    Only bytes up to the last newline are read, so an event still being written
    is simply not there yet — it never arrives half-decoded. A line that is
    complete but unreadable is skipped for the same reason: one corrupt record
    must not cost a reader the whole session.
    """
    source = Path(path)
    if not source.exists():
        return
    data = source.read_bytes()
    end = data.rfind(b"\n")
    if end < 0:
        return
    for line in data[:end].decode("utf-8", errors="replace").splitlines():
        record = _loads(line)
        if record is None:
            continue
        event = decode(record)
        if event is not None:
            yield event


# --- helpers ---------------------------------------------------------------


def _loads(line: str) -> dict[str, Any] | None:
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _ts(value: Any) -> Timestamp | None:
    return Timestamp(value) if isinstance(value, str) and value else None


def _usage_fields(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
    }


def _usage_from(record: Any) -> Usage:
    if not isinstance(record, dict):
        return Usage()

    def count(key: str) -> int:
        value = record.get(key)
        return value if isinstance(value, int) else 0

    return Usage(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_read_input_tokens=count("cache_read_input_tokens"),
        cache_creation_input_tokens=count("cache_creation_input_tokens"),
    )


__all__ = [
    "EVENT_LOG_JSONL",
    "EVENT_SCHEMA_VERSION",
    "append_event",
    "decode",
    "encode",
    "read_events",
    "signal_fields",
    "write_events",
]
