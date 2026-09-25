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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..canonical.events import (
    Event,
    GateVerdict,
    ItemEmitted,
    PersonAsked,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    ToolResultReceived,
    TurnEnded,
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
    AskKind,
    Completion,
    EpochSeconds,
    GateDecision,
    GatePoint,
    ModelName,
    PromptId,
    PromptOrigin,
    ResponseId,
    TextItem,
    ThinkingItem,
    Timestamp,
    ToolCallId,
    ToolCallItem,
    Usage,
    now,
)

#: What ``Signal.source`` says when this reader is the one that spoke. The live
#: SDK reader sees different fields off the same run, so the two are told apart.
SOURCE = "claude/jsonl"
#: The same, for what this reader read off the stream-json wire (``feed``).
WIRE_SOURCE = "claude/wire"

# The top-level ``type`` values read for what happened in the run.
_READ_RECORD_TYPES = frozenset({"assistant", "attachment", "system", "user"})

# Measured top-level types that say nothing about the run, by group.
_SILENT_RECORD_TYPES = frozenset(
    {
        # the session's own identity, title and UI state
        "agent-name",
        "agent-setting",
        "ai-title",
        "artifact-autoreact-ledger",
        "artifact-comment-monitor",
        "atis-latch",
        "bridge-session",
        "fork-context-ref",
        "frame-link",
        "last-prompt",
        "pr-link",
        "relocated",
        "worktree-state",
        # file backups behind /rewind
        "file-history-delta",
        "file-history-snapshot",
        # the permission mode, repeated per prompt and never seen to change mid-session
        "mode",
        "permission-mode",
        # a prompt announced before it lands as a `user` record
        "queue-operation",
        # the one USD figure; per-response usage already sums the run
        "cost-state",
    }
)

#: Every top-level ``type`` the corpus produces; anything else is reported as
#: ``UNSUPPORTED_RECORD`` (R5). ``summary`` is deliberately absent — the legacy
#: ``usage._KNOWN_TYPES`` lists it and it occurs zero times.
KNOWN_RECORD_TYPES = _READ_RECORD_TYPES | _SILENT_RECORD_TYPES

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

# `attachment` subtypes that say a gate of ours ran and delivered no verdict.
_HOOK_FAILURES = frozenset({"hook_cancelled", "hook_non_blocking_error"})

# Measured `attachment` subtypes that say nothing about the run: context the
# harness injects, advice to the model, or a fact another event already carries
# (the triage per subtype: docs/adr/attachments/live-log-surface-survey.md).
_SILENT_ATTACHMENTS = frozenset(
    {
        "agent_listing_delta",
        "auto_mode",
        "auto_mode_exit",
        "bash_output_audience_note",
        "batching_reminder_sent",
        "command_permissions",
        "compact_file_reference",
        "credential_org",  # which organization the credential belongs to
        "date",
        "date_change",
        "deferred_tools_delta",
        "deferred_tools_record",
        "edited_text_file",
        "environment",
        "file",
        "hook_additional_context",  # the chain's own GateVerdict.nudges
        "hook_success",  # the chain's own GateVerdict; a Stop is stop_hook_summary
        "instructions",
        "invoked_skills",
        "mcp_instructions_delta",
        "model",  # ResponseStarted.model, per call
        "plan_mode_exit",
        "prompt_snapshot",
        "queued_command",  # lands as a `user` record → PromptReceived
        "read_truncation_notice",
        "remote_session_change",
        "session_context",
        "silent_turn_reminder",
        "skill_listing",
        "task_reminder",
        "total_tokens_reminder",  # the per-turn cap, reset at every prompt; not quota
    }
)

# Content blocks that are known but carry nothing the canonical items model.
_IGNORED_BLOCKS = frozenset({"image"})

# --- the stream-json wire (``feed``), measured on 2.1.281 -------------------

# Fields the wire spells its own way, renamed before the record reading runs.
_WIRE_RENAMES = {
    "request_id": "requestId",
    "is_api_error_message": "isApiErrorMessage",
    "isSynthetic": "isMeta",
}
# Wire lines that say nothing the log records: the per-command queue (the turn's
# prompts ride its `result`).
_SILENT_WIRE_TYPES = frozenset({"command_lifecycle"})
# `system` subtypes only the wire has: the binary's bookkeeping and UI lines.
_SILENT_WIRE_SUBTYPES = frozenset(
    {
        "background_tasks_changed",
        "hook_started",
        "init",
        "notification",
        "permission_denied",  # the refused call's is_error result says it, in both modes
        "status",
        "task_notification",
        "task_progress",
        "task_started",
        "task_updated",
        "thinking_tokens",
    }
)
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

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

# The tools through which the model asks a person and stops to hear the
# answer — Claude's spelling. A call to one opens a wait a controller must see.
_QUESTION_TOOLS = frozenset({"AskUserQuestion"})

# Who wrote a prompt, by the record's own fields. ``sdk`` is the harness: on
# that path ai-hats plays the person. Unlisted or absent stays unknown.
_PROMPT_ORIGINS: dict[str, PromptOrigin] = {
    "typed": PromptOrigin.PERSON,
    "suggestion_accepted": PromptOrigin.PERSON,
    "queued": PromptOrigin.PERSON,
    "system": PromptOrigin.HARNESS,
    "sdk": PromptOrigin.HARNESS,
}

# A refusal by the surface's own gate arrives as an ``is_error`` tool_result
# whose prose names the decider — the only place the transcript says who.
# Verbatim openings, measured over the corpus; the classifier's carries its
# reason after ``Reason:``.
_CLASSIFIER_DENY = "Permission for this action was denied by the Claude Code auto mode classifier."
_PERSON_DENY = "The user doesn't want to proceed with this tool use."
_DENY_REASON = re.compile(r"Reason: (.+?\.)(?:\s|$)")


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
    ``close()``. A reader with no ``path`` reads only what ``feed`` hands it.
    """  # comment-length: allow — when a response ends IS the contract

    def __init__(self, path: Path | str | None, *, live: bool = False) -> None:
        self._path = None if path is None else Path(path)
        self._live = live
        self._offset = 0
        self._closed = False
        self._drained = False
        self._open: _OpenResponse | None = None
        # identity of every call whose usage has been counted, so a repeated id
        # can never bill twice even if the surface reorders fragments
        self._counted: set[ResponseId] = set()
        self._ended: set[ResponseId] = set()
        # every call announced, with its tool name: a result whose call we never
        # saw is a gap worth reporting, and a refusal names the tool refused
        self._seen_calls: dict[ToolCallId, str] = {}
        # a fork's record opens with the parent's spawning call replayed; the
        # parent's record is that call's producer, so the replay is skipped
        self._in_fork_head = False
        self._replayed_calls: set[str] = set()
        # the quota as the wire last reported it; each quota line replaces it
        self._quota: dict[str, Any] = {}
        self._on_wire = False

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

    # -- the wire ------------------------------------------------------------

    def feed(self, line: Mapping[str, Any]) -> Iterator[Event]:
        """Read one line of claude's stream-json stdout as the main agent's record.

        A record line gets the record reading; the wire alone ends a response
        (``message_delta``) and a turn (``result``). A sub-agent's line is
        skipped: its own record is followed, and one fact has one producer.
        """
        self._on_wire = True
        for event in self._wire_events(line):
            if getattr(event, "source", None) == SOURCE:
                event = replace(event, source=WIRE_SOURCE)  # type: ignore[call-arg]
            yield self._with_quota_reset(event)

    def _with_quota_reset(self, event: Event) -> Event:
        """A wall with no reset takes it from the quota: the wire's API error
        carries none, and the quota line says when a rejection lifts."""
        if not isinstance(event, HarnessActionRequired) or event.retry_after is not None:
            return event
        resets = self._quota.get("resetsAt")
        if self._quota.get("status") != "rejected" or not isinstance(resets, int):
            return event
        return replace(event, retry_after=EpochSeconds(resets))

    def _wire_events(self, line: Mapping[str, Any]) -> Iterator[Event]:
        if not isinstance(line, Mapping):
            yield self._notice("non-object-line")
            return
        if line.get("parent_tool_use_id"):
            return
        record = _from_wire(line)
        rtype, subtype = record.get("type"), record.get("subtype")
        if rtype in _SILENT_WIRE_TYPES or (rtype == "system" and subtype in _SILENT_WIRE_SUBTYPES):
            return
        match rtype:
            case "rate_limit_event":
                yield from self._quota_line(record)
            case "stream_event":
                yield from self._stream_event(record)
            case "result":
                yield from self._result(record)
            case "system" if subtype == "hook_response":
                yield from self._hook_response(record)
            case _:
                yield from self._record(record)

    def _quota_line(self, record: dict[str, Any]) -> Iterator[Event]:
        """The quota's state. ``rejected`` says nothing itself: the wall's producer
        is the refused turn's API error, which reads its reset from here."""
        info = record.get("rate_limit_info")
        if not isinstance(info, dict):
            return
        self._quota = info
        notice = approaching_limit(info)
        if notice is not None:
            yield replace(notice, source=SOURCE, ts=_ts(record))

    def _hook_response(self, record: dict[str, Any]) -> Iterator[Event]:
        """One hook the surface ran. A Stop hook is read as the record reads
        its ``stop_hook_summary`` — one verdict per hook here, one for all
        there; any other hook speaks only when it failed without blocking."""
        ts = _ts(record)
        code = record.get("exit_code")
        name = str(record.get("hook_name") or record.get("hook_event") or "")
        stderr = str(record.get("stderr") or "").strip()
        if record.get("hook_event") == "Stop":
            ended, reason = _ends_the_run(record.get("stdout"))
            yield GateVerdict(
                point=GatePoint.AT_STOP,
                decision=GateDecision.DENY if ended else GateDecision.ALLOW,
                reason=reason,
                source=SOURCE,
                ts=ts,
            )
            if code != 0:
                yield self._warning(stderr or f"{name} exit {code}", ts)
        elif isinstance(code, int) and code not in (0, 2):
            yield self._warning(f"{name} exit {code}: {stderr}", ts)

    @staticmethod
    def _warning(detail: str, ts: Timestamp | None) -> Notice:
        return Notice(
            ts=ts,
            detail=detail,
            raw_code="system/hook_response",
            source=SOURCE,
            reason=WorthRecording.SURFACE_WARNING,
        )

    def _stream_event(self, record: dict[str, Any]) -> Iterator[Event]:
        """``message_delta`` carries the response's final usage and stop reason,
        after its last fragment; every other stream event is a partial message."""
        event = record.get("event")
        if not isinstance(event, dict) or event.get("type") != "message_delta":
            return
        state = self._open
        if state is None:
            yield self._notice("stream_event/message_delta-without-response", ts=_ts(record))
            return
        usage = event.get("usage")
        if isinstance(usage, dict):
            state.usage = _overlay_usage(state.usage, usage)
        delta = event.get("delta")
        stop_reason = delta.get("stop_reason") if isinstance(delta, dict) else None
        if isinstance(stop_reason, str) and stop_reason:
            state.stop_reason = stop_reason
            state.completion = _STOP_REASONS.get(stop_reason, Completion.UNKNOWN)
        yield from self._end_open()

    def _result(self, record: dict[str, Any]) -> Iterator[Event]:
        """The turn is over. A response still open ends first, so every event of
        the turn precedes its ``TurnEnded`` even when no ``message_delta`` came."""
        yield from self._end_open()
        failed = bool(record.get("is_error"))
        word = record.get("terminal_reason") or record.get("subtype")
        text = record.get("result")
        ids = record.get("user_message_uuids")
        yield TurnEnded(
            ok=not failed,
            raw_code=word if isinstance(word, str) else None,
            detail=text if failed and isinstance(text, str) else None,
            prompt_ids=tuple(
                PromptId(i) for i in (ids if isinstance(ids, list) else []) if isinstance(i, str)
            ),
            ts=_ts(record),
        )

    # -- lines -------------------------------------------------------------

    @property
    def _final(self) -> bool:
        """Whether the tail may be closed out rather than held for more."""
        return self._closed or not self._live

    def _pending_lines(self) -> Iterator[str]:
        if self._path is None or not self._path.exists():
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
        # Only a newline ends a record: the surface writes U+2028 raw inside
        # strings, and `splitlines` would cut the record there.
        for raw in data.decode("utf-8", errors="replace").split("\n"):
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
        yield from self._record(record)

    def _record(self, record: dict[str, Any]) -> Iterator[Event]:
        rtype = record.get("type")
        if rtype not in KNOWN_RECORD_TYPES:
            yield self._notice(str(rtype), ts=_ts(record))
            return
        if rtype == "fork-context-ref":
            self._in_fork_head = True
        if rtype in _SILENT_RECORD_TYPES:
            return

        match rtype:
            case "assistant" if self._in_fork_head:
                self._replayed_calls.update(_tool_use_ids(record))
            case "assistant":
                yield from self._assistant_events(record)
            case "user" if self._in_fork_head:
                self._in_fork_head = False
                yield from self._user_events(self._without_replayed_results(record))
            case "user":
                yield from self._user_events(record)
            case "system":
                yield from self._system_events(record)
            case "attachment":
                yield from self._attachment_events(record)

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
                self._seen_calls[call_id] = str(block.get("name", ""))
                inputs = block.get("input")
                inputs = inputs if isinstance(inputs, dict) else {}
                name = str(block.get("name", ""))
                yield ItemEmitted(
                    state.response_id, ToolCallItem(call_id=call_id, name=name, input=inputs), ts
                )
                if name in _QUESTION_TOOLS:
                    yield PersonAsked(
                        kind=AskKind.QUESTION,
                        call_id=call_id,
                        tool=name,
                        detail=_questions(inputs),
                        source=SOURCE,
                        ts=ts,
                    )
            case "fallback":
                # The API rerouted this one call: the block names both models.
                yield Notice(
                    ts=ts,
                    detail=_fallback_detail(block),
                    raw_code="block:fallback",
                    source=SOURCE,
                    reason=WorthRecording.MODEL_SWITCHED,
                    model=_model(block.get("to")),
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

        origin = _prompt_origin(record)
        prompt_id = _prompt_id(record)
        if isinstance(content, str):
            yield from self._prompt(content, ts, origin, prompt_id)
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
        yield from self._prompt(text, ts, origin, prompt_id)

    def _without_replayed_results(self, record: dict[str, Any]) -> dict[str, Any]:
        """``record`` less the results of calls replayed from the parent: what is
        left of a fork's first user record is its task."""
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return record
        kept = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in self._replayed_calls
            )
        ]
        return {**record, "message": {**message, "content": kept}}

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
        ok = not bool(block.get("is_error"))
        if not ok:
            verdict = self._refusal(call_id, block.get("content"), ts)
            if verdict is not None:
                yield verdict
        yield ToolResultReceived(call_id=call_id, ok=ok, content=block.get("content"), ts=ts)

    def _refusal(
        self, call_id: ToolCallId, content: Any, ts: Timestamp | None
    ) -> GateVerdict | None:
        """The surface's own gate said no — a person, or the auto-mode
        classifier. A verdict, so a controller can tell a refusal from a tool
        that merely failed; ``None`` for any other error."""
        text = _first_text(content) or ""
        if text.startswith(_CLASSIFIER_DENY):
            hook = "auto-mode-classifier"
            found = _DENY_REASON.search(text)
            reason = found.group(1) if found else ""
        elif text.startswith(_PERSON_DENY):
            if self._on_wire:
                # a person refuses through the holder, in its words; this prose is an interrupt
                return None
            hook, reason = "person", ""
        else:
            return None
        return GateVerdict(
            point=GatePoint.BEFORE_TOOL,
            decision=GateDecision.DENY,
            hook=hook,
            reason=reason,
            tool=self._seen_calls.get(call_id) or None,
            call_id=call_id,
            source=SOURCE,
            ts=ts,
        )

    def _prompt(
        self,
        text: str,
        ts: Timestamp | None,
        origin: PromptOrigin | None,
        prompt_id: PromptId | None,
    ) -> Iterator[Event]:
        # A prompt does not end the call in flight: queued input lands between
        # fragments 103 times in the measured corpus, and closing there would
        # re-open the call and bill it twice.
        if text.strip():
            yield PromptReceived(text=text, ts=ts, origin=origin, prompt_id=prompt_id)

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

    def _attachment_events(self, record: dict[str, Any]) -> Iterator[Event]:
        """What the harness attached to a turn: read for the one thing in it
        that happened to the run — a hook of ours that failed — and classified
        for the rest, so a subtype nobody measured is drift, not silence."""
        attachment = record.get("attachment")
        ts = _ts(record)
        if not isinstance(attachment, dict):
            yield self._notice("attachment/non-object", ts=ts)
            return
        subtype = attachment.get("type")
        if subtype in _HOOK_FAILURES:
            yield Notice(
                ts=ts,
                detail=_hook_failure(attachment),
                raw_code=f"attachment/{subtype}",
                source=SOURCE,
                reason=WorthRecording.SURFACE_WARNING,
            )
        elif subtype not in _SILENT_ATTACHMENTS:
            yield self._notice(f"attachment/{subtype}", ts=ts)

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


def _from_wire(line: Mapping[str, Any]) -> dict[str, Any]:
    """A wire line in the record's spelling, so one reading serves both."""
    record = {_WIRE_RENAMES.get(key, key): value for key, value in line.items()}
    if record.get("isReplay") and "promptSource" not in record:
        record["promptSource"] = "sdk"  # the echo of a prompt the harness sent
    record.setdefault("timestamp", now())  # most wire lines carry no time of their own
    return record


def _ends_the_run(stdout: Any) -> tuple[bool, str]:
    """Whether a hook's JSON answer says ``continue: false`` — what the record
    calls ``preventedContinuation`` — and the ``stopReason`` it gave."""
    try:
        answer = json.loads(stdout) if isinstance(stdout, str) and stdout.strip() else None
    except ValueError:
        return False, ""  # plain text on stdout is output, not an answer
    if not isinstance(answer, dict) or answer.get("continue") is not False:
        return False, ""
    reason = answer.get("stopReason")
    return True, reason if isinstance(reason, str) else ""


def _overlay_usage(usage: Usage, raw: dict[str, Any]) -> Usage:
    counts = {k: raw[k] for k in _USAGE_FIELDS if isinstance(raw.get(k), int)}
    return replace(usage, **counts)


def _ts(record: dict[str, Any]) -> Timestamp | None:
    value = record.get("timestamp")
    return Timestamp(value) if isinstance(value, str) and value else None


def _prompt_id(record: dict[str, Any]) -> PromptId | None:
    """The record's own uuid: on the wire, the id the harness sent the prompt with."""
    value = record.get("uuid")
    return PromptId(value) if isinstance(value, str) and value else None


def approaching_limit(info: Mapping[str, Any]) -> Notice | None:
    """A ``Notice`` when the quota ``info`` (the wire's ``rate_limit_info``) says
    the limit is close; ``None`` for any other state, the wall included."""
    if info.get("status") != "allowed_warning":
        return None
    window = info.get("rateLimitType")
    parts = [f"{window or 'rate limit'} allowed_warning"]
    windows = info.get("unifiedWindows")
    per_window = windows.get(window) if isinstance(windows, dict) else None
    utilization = info.get("utilization")
    if utilization is None and isinstance(per_window, dict):
        utilization = per_window.get("utilization")
    if isinstance(utilization, (int, float)):
        parts.append(f"utilization {utilization:.0%}")
    if info.get("resetsAt"):
        parts.append(f"resets at {info['resetsAt']}")
    if info.get("overageDisabledReason"):
        parts.append(f"overage unavailable: {info['overageDisabledReason']}")
    return Notice(
        reason=WorthRecording.APPROACHING_LIMIT,
        detail="; ".join(parts),
        raw_code="allowed_warning",
        source=SOURCE,
    )


def _tool_use_ids(record: dict[str, Any]) -> set[str]:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return set()
    return {
        block["id"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("id"), str)
    }


def _prompt_origin(record: dict[str, Any]) -> PromptOrigin | None:
    """Who wrote it, from the record's own fields; ``isMeta`` marks what the
    harness injected without a source of its own (a skill body)."""
    if record.get("isMeta") is True:
        return PromptOrigin.HARNESS
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "human":
        return PromptOrigin.PERSON
    source = record.get("promptSource")
    return _PROMPT_ORIGINS.get(source) if isinstance(source, str) else None


def _questions(inputs: dict[str, Any]) -> str | None:
    """The questions asked, one per line — what a controller shows a person."""
    questions = inputs.get("questions")
    if not isinstance(questions, list):
        return None
    texts = [
        str(q.get("question"))
        for q in questions
        if isinstance(q, dict) and isinstance(q.get("question"), str) and q.get("question")
    ]
    return "\n".join(texts) or None


def _entry_text(entry: Any) -> str:
    return entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False, default=str)


def _hook_failure(attachment: dict[str, Any]) -> str:
    """One line: which hook, how it failed, which command."""
    hook = attachment.get("hookName") or attachment.get("hookEvent") or "hook"
    command = attachment.get("command")
    where = f" ({command})" if isinstance(command, str) and command else ""
    if attachment.get("type") == "hook_cancelled":
        timeout = attachment.get("timeoutMs")
        timed_out = attachment.get("timedOut") is True
        how = "cancelled"
        if timed_out and isinstance(timeout, int) and not isinstance(timeout, bool):
            how = f"timed out after {timeout} ms"
        elif timed_out:
            how = "timed out"
        return f"{hook} {how}{where}"
    code = attachment.get("exitCode")
    head = f"{hook} exit {code}{where}" if code is not None else f"{hook} failed{where}"
    said = attachment.get("stderr") or attachment.get("stdout")
    return f"{head}: {said}" if isinstance(said, str) and said.strip() else head


def _model(message: Any) -> ModelName | None:
    value = message.get("model") if isinstance(message, dict) else None
    return ModelName(value) if isinstance(value, str) and value else None


def _fallback_detail(block: dict[str, Any]) -> str | None:
    switched = [_model(block.get("from")), _model(block.get("to"))]
    return " → ".join(m or "?" for m in switched) if any(switched) else None


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
