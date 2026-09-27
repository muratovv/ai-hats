"""claude's stream-json wire, as measured on 2.1.281 (docs/adr/attachments/headless-wire-claude.md)."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Mapping

from ai_hats_observe.canonical import (
    Event,
    Notice,
    PromptId,
    PromptOrigin,
    PromptReceived,
    ToolCallId,
    WorthRecording,
)
from ai_hats_observe.canonical.types import now
from ai_hats_observe.commands import Answer, Interrupt, Prompt
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

from ..wire import Control, Question, Withdrawn

# Each makes the wire, picks a different session, or leaves no record to read; `-p` and
# `-r`/`-c` are claude's own short forms here, since they reach the binary after ai-hats'.
_OWNED = (
    "--input-format",
    "--output-format",
    "--print",
    "-p",
    "--permission-prompt-tool",
    "--resume",
    "-r",
    "--continue",
    "-c",
    "--session-id",
    "--no-session-persistence",
)

_SOURCE = "claude/wire"
# the tools through which the model asks a person and reads the answer back
_ANSWERED_TOOLS = frozenset({"AskUserQuestion"})
_DENIED = "The person driving this session denied this call."


class ClaudeWire:
    """``--input-format stream-json --output-format stream-json`` over pipes."""

    launch_args: tuple[str, ...] = (
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        # every question the binary would put to a person comes to the holder instead
        "--permission-prompt-tool",
        "stdio",
        # the prompt's echo with its id; a response's final usage (message_delta);
        # hook runs — each on the wire only with its flag (ADR-0038 D3)
        "--replay-user-messages",
        "--include-partial-messages",
        "--include-hook-events",
    )

    def owned_in(self, args: list[str]) -> list[str]:
        names = (a.split("=", 1)[0] for a in args)
        return [name for name in names if name in _OWNED]

    def encode(self, command: Prompt | Interrupt) -> bytes:
        if isinstance(command, Interrupt):
            # the binary acknowledges each request by its own id
            request = {"subtype": "interrupt"}
            return _line(
                {"type": "control_request", "request_id": str(uuid.uuid4()), "request": request}
            )
        line: dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": command.text},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        if command.id is not None:
            # kept as the record's uuid, echoed, and listed in result.user_message_uuids
            line["uuid"] = command.id
        return _line(line)

    def reply(self, question: Question, answer: Answer) -> bytes:
        if answer.decision == "allow":
            # the input carries what a hook put on the call — a consent ticket among it
            run = dict(question.input)
            if answer.answers is not None and question.takes_answers:
                run["answers"] = dict(answer.answers)
            decision: dict[str, Any] = {"behavior": "allow", "updatedInput": run}
        else:
            decision = {"behavior": "deny", "message": answer.message or _DENIED}
        return _line(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": question.request_id,
                    "response": decision,
                },
            }
        )

    def decoder(self) -> _Decoder:
        return _Decoder()


class _Decoder:
    """The transcript reader, fed the wire: one reading of claude's ``message``
    in both modes (ADR-0037), with no file behind it."""

    def __init__(self) -> None:
        self._reader = ClaudeTranscriptReader(None)
        # sent() runs on the holder's stdin pump, decode() on its stdout pump
        self._lock = threading.Lock()
        self._sent: dict[str, str] = {}
        self._received: set[str] = set()
        self.provider_session_id: str | None = None

    def sent(self, prompt: Prompt) -> None:
        if prompt.id is not None:
            with self._lock:
                self._sent[prompt.id] = prompt.text

    def decode(self, line: Mapping[str, Any]) -> list[Event]:
        if not isinstance(line, Mapping):
            return list(self._reader.feed(line))  # the reader reports a line that is no object
        self._note_session(line)
        taken = self._taken(line)
        if taken is not None:
            return taken
        kind = line.get("type")
        if kind == "control_request":
            subtype = _request(line).get("subtype")
            if subtype == "can_use_tool":
                return []
            # a request nobody here answers: the binary may be waiting on it
            return [
                Notice(
                    reason=WorthRecording.UNSUPPORTED_RECORD,
                    raw_code=f"control_request:{subtype}",
                    source=_SOURCE,
                    ts=now(),
                )
            ]
        if kind in ("control_cancel_request", "control_response"):
            return []
        return list(self._reader.feed(line))

    def control(self, line: Mapping[str, Any]) -> Control | None:
        if not isinstance(line, Mapping):
            return None
        self._note_session(line)
        kind = line.get("type")
        if kind == "control_cancel_request":
            return Withdrawn(str(line.get("request_id", "")))
        request = _request(line)
        if kind != "control_request" or request.get("subtype") != "can_use_tool":
            return None
        reason = request.get("decision_reason")
        return Question(
            request_id=str(line.get("request_id", "")),
            call_id=ToolCallId(str(request.get("tool_use_id", ""))),
            tool=str(request.get("tool_name", "")),
            input=dict(given) if isinstance(given := request.get("input"), Mapping) else {},
            reason=reason if isinstance(reason, str) and reason else None,
            source=_SOURCE,
            takes_answers=str(request.get("tool_name", "")) in _ANSWERED_TOOLS,
        )

    def close(self) -> list[Event]:
        self._reader.close()
        return list(self._reader.read())

    def _note_session(self, line: Mapping[str, Any]) -> None:
        # /clear moves the binary to a new session, and every later line names it (2.1.283)
        session_id = line.get("session_id")
        if isinstance(session_id, str) and session_id and not line.get("parent_tool_use_id"):
            self.provider_session_id = session_id

    def _taken(self, line: Mapping[str, Any]) -> list[Event] | None:
        """A sent prompt's receipt at the first sign the binary took it, else ``None``.

        The command's ``started`` comes first for a turn of its own — a local
        command's echo only follows its answer, a refused one has none — and the
        echo first for a prompt folded into a running turn (2.1.283)."""
        kind = line.get("type")
        if kind == "command_lifecycle" and line.get("state") == "started":
            prompt_id = line.get("command_uuid")
        elif kind == "user" and line.get("isReplay") is True:
            prompt_id = line.get("uuid")
        else:
            return None
        if not isinstance(prompt_id, str):
            return None
        with self._lock:
            text = self._sent.pop(prompt_id, None)
            if text is None:
                # the later sign of a prompt already announced says nothing new
                return [] if prompt_id in self._received else None
            self._received.add(prompt_id)
        return [
            PromptReceived(
                text=text, ts=now(), origin=PromptOrigin.PERSON, prompt_id=PromptId(prompt_id)
            )
        ]


def _request(line: Mapping[str, Any]) -> Mapping[str, Any]:
    request = line.get("request")
    return request if isinstance(request, Mapping) else {}


def _line(body: Mapping[str, Any]) -> bytes:
    return (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")


__all__ = ["ClaudeWire"]
