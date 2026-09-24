"""claude's stream-json wire, as measured on 2.1.281 (docs/adr/attachments/headless-wire-claude.md)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ai_hats_observe.canonical import TurnEnded
from ai_hats_observe.canonical.types import now

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
    )

    def owned_in(self, args: list[str]) -> list[str]:
        names = (a.split("=", 1)[0] for a in args)
        return [name for name in names if name in _OWNED]

    def prompt_line(self, text: str) -> bytes:
        line = {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        return (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

    def turn_end(self, message: Mapping[str, Any]) -> TurnEnded | None:
        if message.get("type") != "result":
            return None
        failed = bool(message.get("is_error"))
        word = message.get("terminal_reason") or message.get("subtype")
        text = message.get("result")
        return TurnEnded(
            ok=not failed,
            raw_code=word if isinstance(word, str) else None,
            detail=text if failed and isinstance(text, str) else None,
            ts=now(),
        )


__all__ = ["ClaudeWire"]
