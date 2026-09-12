"""The live SDK message stream, read as canonical session events.

The JSONL transcript and the SDK stream are two views of one run, so both are
read into the vocabulary in ``ai_hats_observe.canonical`` — a consumer written
against the events never learns which of the two produced them. The protocol
lives in ``ai_hats_observe``; surfaces implement it here, never the reverse.

Beyond parity: an API failure arrives as an ``AssistantMessage`` carrying
``error``, and its prose ("You've hit your monthly spend limit") is not an
answer. Here it becomes a signal and never an item.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Iterable

from ai_hats_observe.canonical import (
    Completion,
    EpochSeconds,
    Event,
    HarnessActionRequired,
    HarnessMustAct,
    ItemEmitted,
    ModelName,
    Notice,
    PersonActionRequired,
    PersonMustAct,
    PromptReceived,
    ResponseEnded,
    ResponseId,
    ResponseStarted,
    Signal,
    TextItem,
    ThinkingItem,
    ToolCallId,
    ToolCallItem,
    ToolResultItem,
    Usage,
    WorthRecording,
)

__all__ = ["SOURCE", "ClaudeStreamReader"]


# Which reader spoke. Only this one sees quota pre-warnings; only the transcript
# reader sees a finished session's status.
SOURCE = "claude/sdk"


@lru_cache(maxsize=1)
def _sdk():
    """The SDK module, imported once and lazily — importing this module must not
    cost the framework the SDK's import time."""
    import claude_agent_sdk

    return claude_agent_sdk


# --- fixed mappings --------------------------------------------------------

# ``AssistantMessageError`` is a closed six-member literal, and each member says
# whose problem it is. Identical on the transcript side: one failure, one
# reading, whichever surface reports it.
_ERROR_SIGNALS: dict[str, tuple[Any, Any]] = {
    "authentication_failed": (PersonActionRequired, PersonMustAct.REAUTHENTICATE),
    "billing_error": (PersonActionRequired, PersonMustAct.PAY),
    "rate_limit": (HarnessActionRequired, HarnessMustAct.WAIT),
    "server_error": (HarnessActionRequired, HarnessMustAct.RETRY),
    "invalid_request": (HarnessActionRequired, HarnessMustAct.ABORT),
    "unknown": (HarnessActionRequired, HarnessMustAct.ABORT),
}

_STOP_REASON_COMPLETIONS: dict[str, Completion] = {
    "end_turn": Completion.COMPLETE,
    "stop_sequence": Completion.COMPLETE,
    # The model ended its turn to ask for a tool — finished on its own terms.
    "tool_use": Completion.COMPLETE,
    "max_tokens": Completion.TRUNCATED_BUDGET,
    "refusal": Completion.REFUSED,
}

# ``ResultMessage.terminal_reason`` values that mean the turn was cancelled
# (``interrupt``). The vocabulary has no CANCELLED, and what was received is a
# partial answer cut off mid-stream, so they read as TRUNCATED_TRANSPORT.
_CANCELLED_TERMINAL_REASONS = frozenset({"aborted_streaming", "aborted_tools"})

# Recognised and deliberately silent: routine lifecycle chatter with no canonical
# meaning. Reporting it as UNSUPPORTED_RECORD would fire every run and drown the
# one signal that exists to make real schema drift visible.
_SILENT_SYSTEM_SUBTYPES = frozenset(
    {
        "init",
        "task_started",
        "task_progress",
        "task_notification",
        "task_updated",
    }
)

# Longest forensic blob we put in ``detail`` — enough to identify what happened,
# bounded so a signal never carries a transcript.
_DETAIL_CAP = 500


# --- the reader ------------------------------------------------------------


@dataclass
class _OpenResponse:
    """The inference call currently producing items.

    Held open rather than closed per message because the CLI splits one API
    response into several ``AssistantMessage`` records that share a
    ``message_id`` and repeat that call's usage. Cost is emitted once, when the
    call closes — summing the fragments is the over-count this model exists to
    kill.
    """

    response_id: ResponseId
    model: ModelName | None = None
    usage: Usage = Usage()
    stop_reason: str | None = None


class ClaudeStreamReader:
    """``AsyncEventReader`` over a Claude Agent SDK message stream.

    Takes whatever yields SDK messages — ``client.receive_response()``, or a
    list of messages already drained (``drain_one_turn`` keeps one). The reader
    holds its position, so a second ``read()`` resumes rather than replaying:
    every event is emitted exactly once.

    A response still being produced has no ``ResponseEnded`` yet; there is no
    in-flight value to confuse with an outcome the surface reported.
    """

    def __init__(self, messages: AsyncIterator[Any] | Iterable[Any]) -> None:
        self._source = _as_async_iterator(messages)
        self._exhausted = False
        self._open: _OpenResponse | None = None
        # Responses whose ResponseStarted was already emitted — never announced twice.
        self._started: set[ResponseId] = set()
        # call_id -> the response that asked for it, so an outcome is attributed
        # to its request rather than to position in the stream.
        self._call_owner: dict[ToolCallId, ResponseId] = {}
        self._last_response: ResponseId | None = None
        # Whether a blocking signal already went out, so the ResultMessage that
        # reports the same failure again does not double-announce it.
        self._blocked = False
        self._synthetic = 0

    async def read(self) -> AsyncIterator[Event]:
        """Yield events for everything the stream has produced since the last call."""
        async for message in self._source:
            for event in self._events_for(message):
                yield event
        # The stream is over: whatever was still producing gets its one ending.
        for event in self._close_open(None):
            yield event
        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    # --- dispatch ----------------------------------------------------------

    def _events_for(self, message: Any) -> list[Event]:
        sdk = _sdk()
        match message:
            case sdk.AssistantMessage():
                return self._from_assistant(message)
            case sdk.UserMessage():
                return self._from_user(message)
            # Every typed system message subclasses SystemMessage, and the SDK
            # returns the generic class for the subtypes it does not type, so
            # the whole family is read by subtype.
            case sdk.SystemMessage():
                return self._from_system(message)
            case sdk.ResultMessage():
                return self._from_result(message)
            case sdk.RateLimitEvent():
                return self._from_rate_limit(message)
        # StreamEvent today; whatever the SDK adds tomorrow. Reported, not dropped.
        return [self._unsupported(type(message).__name__)]

    # --- assistant ---------------------------------------------------------

    def _from_assistant(self, message: Any) -> list[Event]:
        if message.error:
            # Contamination rule: a message carrying ``error`` contributes no
            # item — its prose explains a failure, so it goes to the signal's
            # detail, and the failed call is not counted as an inference call.
            return [self._error_signal(message)]

        response_id = self._response_id(message)
        events = self._open_response(response_id, message)
        for block in message.content or []:
            events.extend(self._from_block(response_id, block))
        self._record_call_facts(response_id, message)
        return events

    def _error_signal(self, message: Any) -> Signal:
        factory, reason = _ERROR_SIGNALS.get(
            message.error, (HarnessActionRequired, HarnessMustAct.ABORT)
        )
        detail = _cap(_assistant_text(message)) or None
        return self._blocking(
            factory(reason=reason, detail=detail, raw_code=message.error, source=SOURCE)
        )

    def _open_response(self, response_id: ResponseId, message: Any) -> list[Event]:
        if self._open is not None and self._open.response_id == response_id:
            return []
        events = self._close_open(None)
        self._last_response = response_id
        if response_id in self._started:
            # A call we already closed, speaking again. Its cost and outcome are
            # reported; re-announcing either would break exactly-once, so only
            # its items still count.
            return events
        self._started.add(response_id)
        self._open = _OpenResponse(
            response_id=response_id,
            model=ModelName(message.model) if message.model else None,
        )
        events.append(ResponseStarted(response_id=response_id, model=self._open.model))
        return events

    def _record_call_facts(self, response_id: ResponseId, message: Any) -> None:
        """Keep this call's cost and outcome for the ``ResponseEnded`` that closes it."""
        if self._open is None or self._open.response_id != response_id:
            return
        if message.usage:
            # Replaced, never added: each fragment repeats the call's usage.
            self._open.usage = _usage(message.usage)
        if message.stop_reason:
            self._open.stop_reason = message.stop_reason

    def _close_open(self, terminal_reason: str | None) -> list[Event]:
        open_response = self._open
        if open_response is None:
            return []
        self._open = None
        return [
            ResponseEnded(
                response_id=open_response.response_id,
                completion=_completion(open_response.stop_reason, terminal_reason),
                usage=open_response.usage,
                stop_reason=open_response.stop_reason,
            )
        ]

    def _response_id(self, message: Any) -> ResponseId:
        """Identity of the inference call.

        ``message_id`` is the API's id for the call and is shared by every
        fragment of it — which is what lets cost be counted per call. ``uuid`` is
        per record, so it only stands in when there is no ``message_id``.
        """
        if message.message_id:
            return ResponseId(message.message_id)
        if message.uuid:
            return ResponseId(message.uuid)
        self._synthetic += 1
        return ResponseId(f"response-{self._synthetic}")

    # --- content blocks ----------------------------------------------------

    def _from_block(self, response_id: ResponseId, block: Any) -> list[Event]:
        sdk = _sdk()
        match block:
            case sdk.TextBlock():
                return [ItemEmitted(response_id, TextItem(text=block.text))]
            case sdk.ThinkingBlock():
                return [ItemEmitted(response_id, ThinkingItem(text=block.thinking))]
            # A server-side tool is still an action the model asked for, named
            # and argued the same way; only the executor differs. Dropping it to
            # a Notice would make an audit understate what the agent did.
            case sdk.ToolUseBlock() | sdk.ServerToolUseBlock():
                self._call_owner[ToolCallId(block.id)] = response_id
                return [
                    ItemEmitted(
                        response_id,
                        ToolCallItem(
                            call_id=ToolCallId(block.id),
                            name=str(block.name),
                            input=block.input or {},
                        ),
                    )
                ]
            case sdk.ToolResultBlock():
                return [
                    self._tool_result(
                        block.tool_use_id,
                        ok=not block.is_error,
                        content=block.content,
                        fallback=response_id,
                    )
                ]
            case sdk.ServerToolResultBlock():
                # No is_error here — the API marks a failure in the payload's own
                # ``type`` (e.g. ``web_search_tool_result_error``).
                return [
                    self._tool_result(
                        block.tool_use_id,
                        ok=_server_result_ok(block.content),
                        content=block.content,
                        fallback=response_id,
                    )
                ]
        return [self._unsupported(type(block).__name__)]

    def _tool_result(
        self,
        tool_use_id: str,
        *,
        ok: bool,
        content: Any,
        fallback: ResponseId | None = None,
    ) -> Event:
        call_id = ToolCallId(tool_use_id)
        response_id = self._call_owner.get(call_id) or fallback or self._current_response()
        if response_id is None:
            return self._unsupported(
                "tool_result", detail=f"outcome for {tool_use_id} before any response"
            )
        return ItemEmitted(response_id, ToolResultItem(call_id=call_id, ok=ok, content=content))

    def _current_response(self) -> ResponseId | None:
        return self._open.response_id if self._open is not None else self._last_response

    # --- user --------------------------------------------------------------

    def _from_user(self, message: Any) -> list[Event]:
        """A prompt, or the harness handing back what a tool produced.

        Both arrive as a user message; only the content says which, so a tool
        result is never read as something a person asked.
        """
        sdk = _sdk()
        content = message.content
        if isinstance(content, str):
            return [PromptReceived(text=content)] if content.strip() else []

        events: list[Event] = []
        prose: list[str] = []
        for block in content or []:
            match block:
                case sdk.TextBlock():
                    prose.append(block.text)
                case sdk.ToolResultBlock():
                    events.append(
                        self._tool_result(
                            block.tool_use_id, ok=not block.is_error, content=block.content
                        )
                    )
                case sdk.ServerToolResultBlock():
                    events.append(
                        self._tool_result(
                            block.tool_use_id,
                            ok=_server_result_ok(block.content),
                            content=block.content,
                        )
                    )
                case _:
                    events.append(self._unsupported(type(block).__name__))
        text = "\n".join(part for part in prose if part.strip())
        if text.strip():
            events.append(PromptReceived(text=text))
        return events

    # --- system ------------------------------------------------------------

    def _from_system(self, message: Any) -> list[Event]:
        subtype = message.subtype or ""
        data = message.data if isinstance(message.data, dict) else {}
        raw_code = f"system/{subtype}"
        match subtype:
            case "model_refusal_fallback":
                model = _first_str(data, "fallback_model", "fallbackModel", "model")
                return [
                    Notice(
                        reason=WorthRecording.MODEL_SWITCHED,
                        model=ModelName(model) if model else None,
                        detail=_detail(data),
                        raw_code=raw_code,
                        source=SOURCE,
                    )
                ]
            case "compact_boundary":
                return [
                    Notice(
                        reason=WorthRecording.CONTEXT_COMPACTED,
                        detail=_detail(data),
                        raw_code=raw_code,
                        source=SOURCE,
                    )
                ]
            case "informational":
                # Only a warning is worth an event; an info-level note is
                # recognised and deliberately silent.
                level = str(data.get("level") or "").lower()
                if level in {"warning", "error"}:
                    return [self._unsupported(raw_code, detail=_detail(data))]
                return []
            case _ if subtype in _SILENT_SYSTEM_SUBTYPES:
                return []
        return [self._unsupported(raw_code, detail=_detail(data))]

    # --- rate limit --------------------------------------------------------

    def _from_rate_limit(self, message: Any) -> list[Event]:
        """Quota state, which only the live stream reports.

        ``allowed_warning`` is the one signal that arrives in time to change what
        a caller does; ``rejected`` is the wall, and ``resets_at`` says when it lifts.
        """
        info = message.rate_limit_info
        status = info.status
        detail = _rate_limit_detail(info)
        if status == "rejected":
            resets_at = info.resets_at
            return [
                self._blocking(
                    HarnessActionRequired(
                        reason=HarnessMustAct.WAIT,
                        retry_after=EpochSeconds(int(resets_at)) if resets_at else None,
                        detail=detail,
                        raw_code=status,
                        source=SOURCE,
                    )
                )
            ]
        if status == "allowed_warning":
            return [
                Notice(
                    reason=WorthRecording.APPROACHING_LIMIT,
                    detail=detail,
                    raw_code=status,
                    source=SOURCE,
                )
            ]
        # "allowed" — capacity is normal. No obligation, nothing to record.
        return []

    # --- result ------------------------------------------------------------

    def _from_result(self, message: Any) -> list[Event]:
        """The run ended.

        A result is run-level, not a response, so it is never a ``ResponseEnded``
        of its own: it closes whatever response was still open (its
        ``terminal_reason`` says whether the turn was cancelled) and reports a
        failure as a signal. Its run-total ``usage`` is deliberately dropped —
        per-response usage already sums to it, and emitting both would bill the
        run twice.
        """
        events = self._close_open(message.terminal_reason)
        signal = self._result_signal(message)
        if signal is not None:
            events.append(signal)
        return events

    def _result_signal(self, message: Any) -> Signal | None:
        failed = bool(message.is_error or message.errors or message.api_error_status)
        if not failed or self._blocked:
            # Already announced upstream (an assistant error, a rejected quota).
            # The same failure is not reported twice.
            return None
        status = message.api_error_status
        raw_code = f"{message.subtype}/http_{status}" if status else message.subtype
        detail = _result_detail(message)
        signal: Signal
        if status in (401, 403):
            signal = PersonActionRequired(
                reason=PersonMustAct.REAUTHENTICATE, detail=detail, raw_code=raw_code, source=SOURCE
            )
        elif status == 402:
            signal = PersonActionRequired(
                reason=PersonMustAct.PAY, detail=detail, raw_code=raw_code, source=SOURCE
            )
        else:
            reason = HarnessMustAct.ABORT
            if status == 429:
                reason = HarnessMustAct.WAIT
            elif status is not None and status >= 500:
                reason = HarnessMustAct.RETRY
            signal = HarnessActionRequired(
                reason=reason, detail=detail, raw_code=raw_code, source=SOURCE
            )
        return self._blocking(signal)

    # --- helpers -----------------------------------------------------------

    def _blocking(self, signal: Signal) -> Signal:
        self._blocked = True
        return signal

    def _unsupported(self, raw_code: str, detail: str | None = None) -> Notice:
        return Notice(
            reason=WorthRecording.UNSUPPORTED_RECORD,
            detail=detail,
            raw_code=raw_code,
            source=SOURCE,
        )


# --- module-level helpers --------------------------------------------------


def _as_async_iterator(messages: AsyncIterator[Any] | Iterable[Any]) -> AsyncIterator[Any]:
    """Read either a live stream or a list of messages already drained."""
    if hasattr(messages, "__aiter__"):
        return messages.__aiter__()
    return _replay(messages)


async def _replay(messages: Iterable[Any]) -> AsyncIterator[Any]:
    for message in messages:
        yield message


def _completion(stop_reason: str | None, terminal_reason: str | None) -> Completion:
    """Why the response stopped. The model's own ``stop_reason`` wins; the run's
    ``terminal_reason`` only answers when the model never said."""
    if stop_reason:
        return _STOP_REASON_COMPLETIONS.get(stop_reason, Completion.UNKNOWN)
    if terminal_reason in _CANCELLED_TERMINAL_REASONS:
        return Completion.TRUNCATED_TRANSPORT
    return Completion.UNKNOWN


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    return Usage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        cache_read_input_tokens=_int(raw.get("cache_read_input_tokens")),
        cache_creation_input_tokens=_int(raw.get("cache_creation_input_tokens")),
    )


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _assistant_text(message: Any) -> str:
    """The prose of a message, for a signal's detail — the one place an errored
    message's text is allowed to land."""
    sdk = _sdk()
    return "".join(
        block.text for block in (message.content or []) if isinstance(block, sdk.TextBlock)
    ).strip()


def _server_result_ok(content: Any) -> bool:
    if isinstance(content, dict):
        return not str(content.get("type") or "").endswith("_error")
    return True


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _detail(data: dict[str, Any]) -> str | None:
    """A message if the payload has one, else the payload itself — capped, so a
    signal stays a signal."""
    message = _first_str(data, "message", "reason", "detail")
    if message:
        return _cap(message)
    if not data:
        return None
    try:
        return _cap(json.dumps(data, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return _cap(str(data))


def _rate_limit_detail(info: Any) -> str:
    parts = [f"{info.rate_limit_type or 'rate limit'} {info.status}"]
    if info.utilization is not None:
        parts.append(f"utilization {info.utilization:.0%}")
    if info.resets_at:
        parts.append(f"resets at {info.resets_at}")
    if info.overage_disabled_reason:
        parts.append(f"overage unavailable: {info.overage_disabled_reason}")
    return "; ".join(parts)


def _result_detail(message: Any) -> str | None:
    parts: list[str] = []
    if message.terminal_reason:
        parts.append(f"terminal_reason={message.terminal_reason}")
    if message.errors:
        parts.append("; ".join(str(error) for error in message.errors))
    if message.permission_denials:
        parts.append(f"{len(message.permission_denials)} permission denial(s)")
    if not parts and message.result:
        parts.append(str(message.result))
    return _cap("; ".join(parts)) or None


def _cap(text: str) -> str:
    return text if len(text) <= _DETAIL_CAP else f"{text[: _DETAIL_CAP - 3]}..."
