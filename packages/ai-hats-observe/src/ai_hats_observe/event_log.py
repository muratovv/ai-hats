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
from pathlib import Path
from typing import Any, Iterable, Iterator

from .canonical.events import (
    Event,
    ItemDelta,
    ItemEmitted,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
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
    Completion,
    EpochSeconds,
    Item,
    ItemKind,
    ModelName,
    ResponseId,
    TextItem,
    ThinkingItem,
    Timestamp,
    ToolCallId,
    ToolCallItem,
    Usage,
)

#: Default name of the artifact inside a session dir. Kept here rather than in
#: ``artifacts.py`` while the schema is new: the writer is the only thing that
#: knows this file exists, and a name promoted too early is a name to migrate.
EVENT_LOG_JSONL = "events.jsonl"

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
        case PromptReceived():
            body = {"event": "prompt_received", "text": event.text, "ts": event.ts}
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
        case PersonActionRequired() | HarnessActionRequired() | Notice():
            body = {"event": "signal", **signal_fields(event)}
        case _:
            raise TypeError(f"no encoding for {type(event).__name__}")
    return {"v": EVENT_SCHEMA_VERSION, **body}


def decode(record: dict[str, Any]) -> Event | None:
    """One line's dict back into an event; ``None`` when the line is not one.

    Never raises: this reads a file another process may be mid-way through
    writing, and a shape we do not recognise is a line to skip, not a read to
    abandon.
    """
    response_id = ResponseId(str(record.get("response_id", "")))
    ts = _ts(record.get("ts"))
    match record.get("event"):
        case "prompt_received":
            return PromptReceived(text=str(record.get("text", "")), ts=ts)
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
        case "signal":
            return _signal_from(record)
    return None


# --- the file ---------------------------------------------------------------


def write_events(events: Iterable[Event], path: Path | str, *, append: bool = False) -> int:
    """Write ``events`` to ``path``, one JSON object per line; return the count.

    ``append`` continues an existing file — the mode a live writer uses, and the
    reason each line is flushed as it is written rather than at close: a reader
    following the file sees an event as soon as it happened.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("a" if append else "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(encode(event), ensure_ascii=False, default=str))
            handle.write("\n")
            handle.flush()
            written += 1
    return written


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
    "decode",
    "encode",
    "read_events",
    "signal_fields",
    "write_events",
]
