"""``commands/v1`` — one line of a headless holder's stdin, read strictly (ADR-0038 D3)."""

from __future__ import annotations

import json
from dataclasses import dataclass

COMMANDS_V1 = "commands/v1"

# Named by ADR-0038 D3 and not built yet: refused as such, never as unknown.
_PLANNED = ("answer", "interrupt")


@dataclass(frozen=True)
class Prompt:
    """A turn for the session."""

    text: str


@dataclass(frozen=True)
class Rejected:
    """A line the holder will not execute; ``cmd`` is its ``cmd`` when it had one."""

    cmd: str | None
    why: str


def parse_command(line: bytes) -> Prompt | Rejected | None:
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
    unknown = sorted(set(body) - {"v", "cmd", "text"})
    if unknown:
        return Rejected(cmd, f'unknown key "{unknown[0]}" — prompt takes: text')
    prompt = body.get("text")
    if not isinstance(prompt, str) or not prompt.strip():
        return Rejected(cmd, '"text" must be a non-empty string')
    return Prompt(prompt)


__all__ = ["COMMANDS_V1", "Prompt", "Rejected", "parse_command"]
