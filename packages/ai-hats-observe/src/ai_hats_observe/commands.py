"""``commands/v1`` — what a headless client says to the holder, as our objects (ADR-0038 D3).

The events half of the contract is ``event_log``; this is the other half, so a
client builds its lines and a holder reads them with one codec. Reading is
strict: a typo is refused out loud with the words to fix it, never lost.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .canonical.types import PromptId, ToolCallId

COMMANDS_V1 = "commands/v1"

#: What a holder executes today; a client reads the same list off the session header.
COMMANDS = ("prompt", "answer")

# Named by ADR-0038 D3 and not built yet: refused as such, never as unknown.
_PLANNED = ("interrupt",)

#: What an ``answer`` may decide.
DECISIONS = ("allow", "deny")

_KEYS = {"prompt": ("text", "id"), "answer": ("call_id", "decision", "message", "answers")}


@dataclass(frozen=True)
class Prompt:
    """A turn for the session. ``id`` finds the turn's end later; ``None`` asks
    the holder to pick one."""

    text: str
    id: PromptId | None = None


@dataclass(frozen=True)
class Answer:
    """A decision on the question ``call_id`` names. ``message`` tells the model
    why a call was denied; ``answers`` maps each question the model asked to its
    answer."""

    call_id: ToolCallId
    decision: str
    message: str | None = None
    answers: dict[str, str] | None = None


Command = Prompt | Answer


@dataclass(frozen=True)
class Rejected:
    """A line the holder will not execute; ``cmd`` is its ``cmd`` when it had one."""

    cmd: str | None
    why: str


def encode_command(command: Command) -> bytes:
    """One command as a line of the holder's stdin."""
    body: dict[str, Any] = {"v": COMMANDS_V1}
    if isinstance(command, Answer):
        body.update(cmd="answer", call_id=command.call_id, decision=command.decision)
        if command.message is not None:
            body["message"] = command.message
        if command.answers is not None:
            body["answers"] = dict(command.answers)
    else:
        body["cmd"] = "prompt"
        if command.id is not None:
            body["id"] = command.id
        body["text"] = command.text
    return (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")


def decode_command(line: bytes) -> Command | Rejected | None:
    """One stdin line → a command, a refusal, or ``None`` for a blank line."""
    if not line.strip():
        return None
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return Rejected(None, "not UTF-8")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        return Rejected(None, f"not JSON ({exc.msg}): {text.strip()[:80]!r}")
    if not isinstance(body, dict):
        return Rejected(None, "not a JSON object")
    known = ", ".join(COMMANDS)
    cmd = body.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        return Rejected(None, f'missing "cmd" — known: {known}; "v" is "{COMMANDS_V1}"')
    version = body.get("v")
    if version is None:
        return Rejected(cmd, f'missing "v" — expected "{COMMANDS_V1}"')
    if version != COMMANDS_V1:
        return Rejected(cmd, f'unsupported "v" {version!r} — this holder speaks {COMMANDS_V1}')
    if cmd in _PLANNED:
        return Rejected(cmd, f"{cmd} is not implemented yet")
    if cmd not in COMMANDS:
        return Rejected(cmd, f'unknown command "{cmd}" — known: {known}')
    unknown = sorted(set(body) - {"v", "cmd", *_KEYS[cmd]})
    if unknown:
        return Rejected(cmd, f'unknown key "{unknown[0]}" — {cmd} takes: {", ".join(_KEYS[cmd])}')
    return _answer(body) if cmd == "answer" else _prompt(body)


def _prompt(body: dict[str, Any]) -> Prompt | Rejected:
    prompt = body.get("text")
    if not isinstance(prompt, str) or not prompt.strip():
        return Rejected("prompt", '"text" must be a non-empty string')
    if "id" not in body:
        return Prompt(prompt)
    given = body["id"]
    if not _canonical_uuid(given):
        return Rejected(
            "prompt", f'"id" must be a UUID in canonical form (lowercase, hyphenated): {given!r}'
        )
    return Prompt(prompt, PromptId(given))


def _answer(body: dict[str, Any]) -> Answer | Rejected:
    call_id = body.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return Rejected("answer", '"call_id" must be the call_id of a person_asked')
    decision = body.get("decision")
    if decision not in DECISIONS:
        return Rejected("answer", f'"decision" must be one of: {", ".join(DECISIONS)}')
    message = body.get("message")
    if message is not None:
        if not isinstance(message, str):
            return Rejected("answer", '"message" must be a string')
        if decision == "allow":
            return Rejected("answer", '"message" goes with a deny; an allow runs the call')
    answers = body.get("answers")
    if answers is not None and not (
        isinstance(answers, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items())
    ):
        return Rejected("answer", '"answers" must be an object of strings: question → answer')
    return Answer(ToolCallId(call_id), decision, message, answers)


def _canonical_uuid(value: object) -> bool:
    # The surface keeps any string as its record's uuid, so the form is ours to hold.
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


__all__ = [
    "COMMANDS",
    "COMMANDS_V1",
    "DECISIONS",
    "Answer",
    "Command",
    "Prompt",
    "Rejected",
    "decode_command",
    "encode_command",
]
