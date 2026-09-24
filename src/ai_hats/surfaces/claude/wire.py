"""claude's stream-json wire, as measured on 2.1.281 (docs/adr/attachments/headless-wire-claude.md)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ai_hats_observe.canonical import Event
from ai_hats_observe.commands import Prompt
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

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


class ClaudeWire:
    """``--input-format stream-json --output-format stream-json`` over pipes."""

    launch_args: tuple[str, ...] = (
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        # the prompt's echo with its id; a response's final usage (message_delta);
        # hook runs — each on the wire only with its flag (ADR-0038 D3)
        "--replay-user-messages",
        "--include-partial-messages",
        "--include-hook-events",
    )

    def owned_in(self, args: list[str]) -> list[str]:
        names = (a.split("=", 1)[0] for a in args)
        return [name for name in names if name in _OWNED]

    def encode(self, command: Prompt) -> bytes:
        line: dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": command.text},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        if command.id is not None:
            # kept as the record's uuid, echoed, and listed in result.user_message_uuids
            line["uuid"] = command.id
        return (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

    def decoder(self) -> _Decoder:
        return _Decoder()


class _Decoder:
    """The transcript reader, fed the wire: one reading of claude's ``message``
    in both modes (ADR-0037), with no file behind it."""

    def __init__(self) -> None:
        self._reader = ClaudeTranscriptReader(None)

    def decode(self, line: Mapping[str, Any]) -> list[Event]:
        return list(self._reader.feed(line))

    def close(self) -> list[Event]:
        self._reader.close()
        return list(self._reader.read())


__all__ = ["ClaudeWire"]
