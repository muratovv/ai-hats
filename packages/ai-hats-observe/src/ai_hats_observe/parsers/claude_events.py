"""Claude Code JSONL → canonical events (HATS-1966, S1+S3).

One JSONL record is a *fragment* of an API response: fragments of one call share
a ``requestId`` and each repeats a byte-identical ``message.usage``, which is
what inflates token telemetry 2.61x when a record is read as a response. This
reader groups by identity instead — one ``ResponseStarted`` per call, one
``ItemEmitted`` per block, one ``ResponseEnded`` carrying the usage taken
**once** — and holds its position, so a grown source yields only what is new.
Nothing here raises: an unreadable line or unmodelled shape becomes a ``Notice``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..canonical.events import (
    Event,
    GateVerdict,
    ItemEmitted,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    ToolResultReceived,
)
from ..canonical.signals import (
    HarnessActionRequired,
    HarnessMustAct,
    Notice,
    PersonActionRequired,
    PersonMustAct,
    Signal,
    WorthRecording,
)
from ..canonical.types import (
    Completion,
    EpochSeconds,
    GateDecision,
    GatePoint,
    ModelName,
    ResponseId,
    TextItem,
    ThinkingItem,
    Timestamp,
    ToolCallId,
    ToolCallItem,
    Usage,
)

#: What ``Signal.source`` says when this reader is the one that spoke. The live
#: SDK reader sees different fields off the same run, so the two are told apart.
SOURCE = "claude/jsonl"

#: Every top-level ``type`` the corpus produces; anything else is reported as
#: ``UNSUPPORTED_RECORD`` (R5). ``summary`` is deliberately absent — the legacy
#: ``usage._KNOWN_TYPES`` lists it and it occurs zero times.
KNOWN_RECORD_TYPES = frozenset(
    {
        "agent-name",
        "agent-setting",
        "ai-title",
        "artifact-autoreact-ledger",
        "artifact-comment-monitor",
        "assistant",
        "atis-latch",
        "attachment",
        "bridge-session",
        "cost-state",
        "file-history-delta",
        "file-history-snapshot",
        "fork-context-ref",
        "frame-link",
        "last-prompt",
        "mode",
        "permission-mode",
        "pr-link",
        "queue-operation",
        "relocated",
        "system",
        "user",
        "worktree-state",
    }
)

# --- mappings --------------------------------------------------------------

# The six values Claude's top-level `error` field can hold, split by who must act.
_PERSON_ERRORS: dict[str, PersonMustAct] = {
    "authentication_failed": PersonMustAct.REAUTHENTICATE,
    "billing_error": PersonMustAct.PAY,
}
_HARNESS_ERRORS: dict[str, HarnessMustAct] = {
    "rate_limit": HarnessMustAct.WAIT,
    "server_error": HarnessMustAct.RETRY,
    "invalid_request": HarnessMustAct.ABORT,
    "unknown": HarnessMustAct.ABORT,
}

_STOP_REASONS: dict[str, Completion] = {
    "end_turn": Completion.COMPLETE,
    "tool_use": Completion.COMPLETE,
    "stop_sequence": Completion.COMPLETE,
    "max_tokens": Completion.TRUNCATED_BUDGET,
    "refusal": Completion.REFUSED,
}

# `system` subtypes we recognise and deliberately say nothing about: they carry
# harness bookkeeping, not run health.
_SILENT_SYSTEM_SUBTYPES = frozenset(
    {
        "away_summary",
        "bridge_status",
        "local_command",
        "scheduled_task_fire",
        "turn_duration",
    }
)

# Content blocks that are known but carry nothing the canonical items model.
_IGNORED_BLOCKS = frozenset({"image"})

#: What the harness writes as user text when a person stops the turn — a
#: marker, not input. Verbatim, no variants in the measured corpus. Shared with
#: the SDK reader: one marker set, one reading, whichever source carried it.
INTERRUPT_MARKERS = frozenset(
    {
        "[Request interrupted by user]",
        "[Request interrupted by user for tool use]",
    }
)

# "…Switched to Opus 4.8. Send feedback…" — the fallback model as prose, read
# only when the record omits the `fallbackModel` field.
_SWITCHED_TO = re.compile(r"Switched to (.+?)\.(?:\s|$)")


# --- reader ----------------------------------------------------------------


@dataclass
class _OpenResponse:
    """A call whose fragments are still arriving."""

    response_id: ResponseId
    model: ModelName | None = None
    usage: Usage = Usage()
    stop_reason: str | None = None
    completion: Completion | None = None
    ts: Timestamp | None = None
    calls: set[ToolCallId] = field(default_factory=set)


class ClaudeTranscriptReader:
    """Read a Claude Code JSONL transcript as canonical events.

    Satisfies ``canonical.reader.EventReader``: it remembers its byte offset, so
    ``read()`` on a grown source yields only the new events, each exactly once.

    A response ends when the next call's first fragment arrives, or when the
    input runs out — the latter can leave a call with no reported outcome, hence
    ``UNKNOWN``. ``live`` says whether the run is still being written: a finished
    record (the default) closes its tail at EOF, a live one holds it open until
    ``close()``.
    """  # comment-length: allow — when a response ends IS the contract

    def __init__(self, path: Path | str, *, live: bool = False) -> None:
        self._path = Path(path)
        self._live = live
        self._offset = 0
        self._closed = False
        self._drained = False
        self._open: _OpenResponse | None = None
        # identity of every call whose usage has been counted, so a repeated id
        # can never bill twice even if the surface reorders fragments
        self._counted: set[ResponseId] = set()
        self._ended: set[ResponseId] = set()
        # membership only: a result whose call we never saw is a gap worth
        # reporting, but the result is parented by call_id, not by response
        self._seen_calls: set[ToolCallId] = set()

    # -- EventReader -------------------------------------------------------

    def read(self) -> Iterator[Event]:
        """Yield events appended since the previous call."""
        self._drained = False
        for line in self._pending_lines():
            yield from self._record_events(line)
        if self._final:
            yield from self._end_open()
            self._drained = True

    @property
    def exhausted(self) -> bool:
        """Whether the source can still produce more.

        A live run says False until ``close()``, so a follower is never told a
        paused run has ended; a finished record says True once drained.
        """
        return self._final and self._drained

    def close(self) -> None:
        """Declare a live run over: the next ``read()`` drains the tail and ends
        any call still open. A no-op for a source already read as finished."""
        self._closed = True
        self._drained = False

    # -- lines -------------------------------------------------------------

    @property
    def _final(self) -> bool:
        """Whether the tail may be closed out rather than held for more."""
        return self._closed or not self._live

    def _pending_lines(self) -> Iterator[str]:
        if not self._path.exists():
            return
        with self._path.open("rb") as handle:
            handle.seek(self._offset)
            data = handle.read()
        if not data:
            return
        if not self._final:
            # A trailing line without its newline may still be half-written.
            cut = data.rfind(b"\n")
            if cut < 0:
                return
            data = data[: cut + 1]
        self._offset += len(data)
        for raw in data.decode("utf-8", errors="replace").splitlines():
            if raw.strip():
                yield raw

    # -- records -----------------------------------------------------------

    def _record_events(self, line: str) -> Iterator[Event]:
        try:
            record = json.loads(line)
        except ValueError:
            yield self._notice("malformed-json")
            return
        if not isinstance(record, dict):
            yield self._notice("non-object-line")
            return

        rtype = record.get("type")
        if rtype not in KNOWN_RECORD_TYPES:
            yield self._notice(str(rtype), ts=_ts(record))
            return

        match rtype:
            case "assistant":
                yield from self._assistant_events(record)
            case "user":
                yield from self._user_events(record)
            case "system":
                yield from self._system_events(record)

    def _assistant_events(self, record: dict[str, Any]) -> Iterator[Event]:
        message = record.get("message")
        message = message if isinstance(message, dict) else {}

        # Contamination rule: a record carrying an api-error contributes no
        # TextItem. Its prose is the signal's detail and nothing else — this is
        # what kept a spend-limit notice from being read as the agent's answer.
        if record.get("isApiErrorMessage"):
            yield _api_error_signal(record, message)
            return

        response_id = _response_id(record, message)
        ts = _ts(record)
        if self._open is None or self._open.response_id != response_id:
            yield from self._end_open()
            if response_id in self._ended:
                # Fragments of one call are contiguous in every transcript
                # measured; an id coming back means the shape changed.
                yield self._notice("repeated-response-id", ts=ts)
            self._open = _OpenResponse(
                response_id=response_id,
                model=_model(message),
                ts=ts,
            )
            yield ResponseStarted(
                response_id=response_id,
                model=self._open.model,
                ts=ts,
            )

        state = self._open
        state.ts = ts or state.ts

        # Usage is identical on every fragment of a call (6166 repeats observed,
        # 0 differing), so it is read once and never summed.
        usage = message.get("usage")
        if isinstance(usage, dict) and response_id not in self._counted:
            self._counted.add(response_id)
            state.usage = _usage(usage)

        if record.get("truncatedAfterOutput") is True:
            state.completion = Completion.TRUNCATED_TRANSPORT
            state.stop_reason = message.get("stop_reason") or state.stop_reason
        else:
            stop_reason = message.get("stop_reason")
            # None is the normal shape of a fragment mid-response, not an
            # outcome — only a fragment that names a reason moves the verdict.
            if isinstance(stop_reason, str) and stop_reason:
                state.stop_reason = stop_reason
                state.completion = _STOP_REASONS.get(stop_reason, Completion.UNKNOWN)

        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                yield from self._assistant_block(state, block, ts)

    def _assistant_block(
        self, state: _OpenResponse, block: Any, ts: Timestamp | None
    ) -> Iterator[Event]:
        if not isinstance(block, dict):
            yield self._notice("block:non-object", ts=ts)
            return
        btype = block.get("type")
        match btype:
            case "text":
                yield ItemEmitted(state.response_id, TextItem(str(block.get("text", ""))), ts)
            case "thinking":
                yield ItemEmitted(
                    state.response_id, ThinkingItem(str(block.get("thinking", ""))), ts
                )
            case "redacted_thinking":
                yield ItemEmitted(
                    state.response_id,
                    ThinkingItem(str(block.get("data", "")), redacted=True),
                    ts,
                )
            case "tool_use":
                call_id = ToolCallId(str(block.get("id", "")))
                if call_id in state.calls:
                    return
                state.calls.add(call_id)
                self._seen_calls.add(call_id)
                inputs = block.get("input")
                yield ItemEmitted(
                    state.response_id,
                    ToolCallItem(
                        call_id=call_id,
                        name=str(block.get("name", "")),
                        input=inputs if isinstance(inputs, dict) else {},
                    ),
                    ts,
                )
            case _ if btype in _IGNORED_BLOCKS:
                return
            case _:
                yield self._notice(f"block:{btype}", ts=ts)

    def _user_events(self, record: dict[str, Any]) -> Iterator[Event]:
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        ts = _ts(record)

        if isinstance(content, str):
            yield from self._prompt(content, ts)
            return
        if not isinstance(content, list):
            return

        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            for block in results:
                yield from self._tool_result(block, ts)
            return

        texts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "\n".join(t for t in texts if t)
        if text.strip() in INTERRUPT_MARKERS:
            yield from self._interrupted(text.strip(), ts)
            return
        yield from self._prompt(text, ts)

    def _interrupted(self, marker: str, ts: Timestamp | None) -> Iterator[Event]:
        """A person stopped the turn. The model's own stop reason wins; only a
        response it never got to close reads as cancelled."""
        state = self._open
        if state is not None and not state.stop_reason:
            state.completion = Completion.CANCELLED
        yield from self._end_open()
        yield Notice(
            ts=ts,
            raw_code=marker,
            source=SOURCE,
            reason=WorthRecording.INTERRUPTED,
        )

    def _tool_result(self, block: dict[str, Any], ts: Timestamp | None) -> Iterator[Event]:
        call_id = ToolCallId(str(block.get("tool_use_id", "")))
        if call_id not in self._seen_calls:
            # An outcome for a call this transcript never recorded is a gap, and
            # saying so beats inventing the link.
            yield self._notice("orphan-tool-result", ts=ts)
            return
        yield ToolResultReceived(
            call_id=call_id,
            ok=not bool(block.get("is_error")),
            content=block.get("content"),
            ts=ts,
        )

    def _prompt(self, text: str, ts: Timestamp | None) -> Iterator[Event]:
        # A prompt does not end the call in flight: queued input lands between
        # fragments 103 times in the measured corpus, and closing there would
        # re-open the call and bill it twice.
        if text.strip():
            yield PromptReceived(text=text, ts=ts)

    def _system_events(self, record: dict[str, Any]) -> Iterator[Event]:
        subtype = record.get("subtype")
        ts = _ts(record)
        content = record.get("content")
        detail = content if isinstance(content, str) else None

        match subtype:
            case "model_refusal_fallback":
                yield Notice(
                    ts=ts,
                    detail=detail,
                    raw_code="system/model_refusal_fallback",
                    source=SOURCE,
                    reason=WorthRecording.MODEL_SWITCHED,
                    model=_fallback_model(record),
                )
            case "compact_boundary":
                yield Notice(
                    ts=ts,
                    detail=detail,
                    raw_code="system/compact_boundary",
                    source=SOURCE,
                    reason=WorthRecording.CONTEXT_COMPACTED,
                )
            case "informational":
                # `notice`/`info` are harness chatter; a `warning` is worth
                # reporting.
                if record.get("level") == "warning":
                    yield Notice(
                        ts=ts,
                        detail=detail,
                        raw_code="system/informational",
                        source=SOURCE,
                        reason=WorthRecording.SURFACE_WARNING,
                    )
            case "stop_hook_summary":
                yield from self._stop_hook_events(record, ts)
            case _ if subtype in _SILENT_SYSTEM_SUBTYPES:
                return
            case _:
                yield self._notice(f"system/{subtype}", ts=ts, detail=detail)

    def _stop_hook_events(self, record: dict[str, Any], ts: Timestamp | None) -> Iterator[Event]:
        """The record Claude writes after its Stop hooks ran: one verdict at the
        stop, and a warning per hook that failed.

        ``hookErrors`` and ``hookAdditionalContext`` are empty in every one of
        400 measured transcripts, so an entry's shape is unobserved and carried
        as text rather than modelled.
        """
        infos = record.get("hookInfos")
        commands = [
            str(info.get("command", ""))
            for info in (infos if isinstance(infos, list) else [])
            if isinstance(info, dict)
        ]
        count = record.get("hookCount")
        if (count if isinstance(count, int) else len(commands)) <= 0:
            return
        blocked = record.get("preventedContinuation") is True
        stop_reason = record.get("stopReason")
        context = record.get("hookAdditionalContext")
        yield GateVerdict(
            point=GatePoint.AT_STOP,
            decision=GateDecision.DENY if blocked else GateDecision.ALLOW,
            # the one hook that ran is the one that blocked; several cannot be told apart
            hook=commands[0].rsplit("/", 1)[-1] if blocked and len(commands) == 1 else "",
            reason=stop_reason if isinstance(stop_reason, str) else "",
            nudges=tuple(
                ("", _entry_text(entry)) for entry in (context if isinstance(context, list) else [])
            ),
            source=SOURCE,
            ts=ts,
        )
        errors = record.get("hookErrors")
        for error in errors if isinstance(errors, list) else []:
            yield Notice(
                ts=ts,
                detail=_entry_text(error),
                raw_code="system/stop_hook_summary",
                source=SOURCE,
                reason=WorthRecording.SURFACE_WARNING,
            )

    # -- helpers -----------------------------------------------------------

    def _end_open(self) -> Iterator[Event]:
        state = self._open
        if state is None:
            return
        self._open = None
        self._ended.add(state.response_id)
        yield ResponseEnded(
            response_id=state.response_id,
            completion=state.completion or Completion.UNKNOWN,
            usage=state.usage,
            stop_reason=state.stop_reason,
            ts=state.ts,
        )

    @staticmethod
    def _notice(raw_code: str, *, ts: Timestamp | None = None, detail: str | None = None) -> Notice:
        return Notice(
            ts=ts,
            detail=detail,
            raw_code=raw_code,
            source=SOURCE,
            reason=WorthRecording.UNSUPPORTED_RECORD,
        )


# --- record-level readers --------------------------------------------------


def _ts(record: dict[str, Any]) -> Timestamp | None:
    value = record.get("timestamp")
    return Timestamp(value) if isinstance(value, str) and value else None


def _entry_text(entry: Any) -> str:
    return entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False, default=str)


def _model(message: dict[str, Any]) -> ModelName | None:
    value = message.get("model")
    return ModelName(value) if isinstance(value, str) and value else None


def _response_id(record: dict[str, Any], message: dict[str, Any]) -> ResponseId:
    """Identity of the call this fragment belongs to.

    ``requestId`` is the real thing; ``message.id`` and ``uuid`` are the
    fallbacks, because 26 of 40 sampled error records carry no ``requestId``.
    """
    for candidate in (record.get("requestId"), message.get("id"), record.get("uuid")):
        if isinstance(candidate, str) and candidate:
            return ResponseId(candidate)
    return ResponseId("unidentified")


def _usage(raw: dict[str, Any]) -> Usage:
    def count(key: str) -> int:
        value = raw.get(key)
        return value if isinstance(value, int) else 0

    return Usage(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_read_input_tokens=count("cache_read_input_tokens"),
        cache_creation_input_tokens=count("cache_creation_input_tokens"),
    )


def _first_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts = [
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    ]
    joined = "\n".join(p for p in parts if p)
    return joined or None


def _fallback_model(record: dict[str, Any]) -> ModelName | None:
    named = record.get("fallbackModel")
    if isinstance(named, str) and named:
        return ModelName(named)
    content = record.get("content")
    if isinstance(content, str):
        hit = _SWITCHED_TO.search(content)
        if hit:
            return ModelName(hit.group(1).strip())
    return None


def _api_error_signal(record: dict[str, Any], message: dict[str, Any]) -> Signal:
    """Map Claude's six-value ``error`` field onto who has to act.

    ``raw_code`` prefers the HTTP status (403/429/529) and falls back to the
    ``error`` value, so the surface's own code is always on the record.
    """
    error = record.get("error")
    error = error if isinstance(error, str) and error else "unknown"
    status = record.get("apiErrorStatus")
    raw_code = str(status) if status is not None else error
    shared = {
        "ts": _ts(record),
        "detail": _first_text(message.get("content")),
        "raw_code": raw_code,
        "source": SOURCE,
    }

    if error in _PERSON_ERRORS:
        return PersonActionRequired(**shared, reason=_PERSON_ERRORS[error])

    reason = _HARNESS_ERRORS.get(error, HarnessMustAct.ABORT)
    retry_after: EpochSeconds | None = None
    quota = record.get("quotaLimits")
    if reason is HarnessMustAct.WAIT and isinstance(quota, dict):
        resets = quota.get("resetsAt")
        if isinstance(resets, int):
            retry_after = EpochSeconds(resets)
    return HarnessActionRequired(**shared, reason=reason, retry_after=retry_after)
