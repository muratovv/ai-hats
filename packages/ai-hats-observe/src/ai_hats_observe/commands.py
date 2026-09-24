"""``commands/v1`` — what a headless client says to the holder, as our objects (ADR-0038 D3).

The events half of the contract is ``event_log``; this is the other half, so a
client builds its lines and a holder reads them with one codec. Reading is
strict: a typo is refused out loud with the words to fix it, never lost.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .canonical.types import PromptId

COMMANDS_V1 = "commands/v1"

#: What a holder executes today; a client reads the same list off the session header.
COMMANDS = ("prompt",)

# Named by ADR-0038 D3 and not built yet: refused as such, never as unknown.
_PLANNED = ("answer", "interrupt")


@dataclass(frozen=True)
class Prompt:
    """A turn for the session. ``id`` finds the turn's end later; ``None`` asks
    the holder to pick one."""

    text: str
    id: PromptId | None = None


@dataclass(frozen=True)
class Rejected:
    """A line the holder will not execute; ``cmd`` is its ``cmd`` when it had one."""

    cmd: str | None
    why: str


def encode_command(command: Prompt) -> bytes:
    """One command as a line of the holder's stdin."""
    body: dict[str, str] = {"v": COMMANDS_V1, "cmd": "prompt"}
    if command.id is not None:
        body["id"] = command.id
    body["text"] = command.text
    return (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")


def decode_command(line: bytes) -> Prompt | Rejected | None:
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
    cmd = body.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        return Rejected(None, f'missing "cmd" — known: prompt; "v" is "{COMMANDS_V1}"')
    version = body.get("v")
    if version is None:
        return Rejected(cmd, f'missing "v" — expected "{COMMANDS_V1}"')
    if version != COMMANDS_V1:
        return Rejected(cmd, f'unsupported "v" {version!r} — this holder speaks {COMMANDS_V1}')
    if cmd in _PLANNED:
        return Rejected(cmd, f"{cmd} is not implemented yet")
    if cmd != "prompt":
        return Rejected(cmd, f'unknown command "{cmd}" — known: prompt')
    unknown = sorted(set(body) - {"v", "cmd", "id", "text"})
    if unknown:
        return Rejected(cmd, f'unknown key "{unknown[0]}" — prompt takes: text, id')
    prompt = body.get("text")
    if not isinstance(prompt, str) or not prompt.strip():
        return Rejected(cmd, '"text" must be a non-empty string')
    if "id" not in body:
        return Prompt(prompt)
    given = body["id"]
    if not _canonical_uuid(given):
        return Rejected(
            cmd, f'"id" must be a UUID in canonical form (lowercase, hyphenated): {given!r}'
        )
    return Prompt(prompt, PromptId(given))


def _canonical_uuid(value: object) -> bool:
    # The surface keeps any string as its record's uuid, so the form is ours to hold.
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


__all__ = ["COMMANDS", "COMMANDS_V1", "Prompt", "Rejected", "decode_command", "encode_command"]
