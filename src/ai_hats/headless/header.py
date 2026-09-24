"""The session header — line 1 of a holder's stdout (``headless/v1``, ADR-0038 D9)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ai_hats_observe.commands import COMMANDS
from ai_hats_observe.event_log import EVENT_SCHEMA_VERSION

HEADLESS_V1 = "headless/v1"


@dataclass(frozen=True)
class SessionHeader:
    """Who this session is and where its log lies — nothing a client could not
    find otherwise, all in one line it reads first."""

    session_id: str
    session_dir: Path
    log: Path
    holder_pid: int
    provider_session_id: str
    started_at: str
    events: str = EVENT_SCHEMA_VERSION
    commands: tuple[str, ...] = COMMANDS

    def line(self) -> bytes:
        body = {
            "v": HEADLESS_V1,
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "log": str(self.log),
            "holder_pid": self.holder_pid,
            "provider_session_id": self.provider_session_id,
            "started_at": self.started_at,
            "events": self.events,
            "commands": list(self.commands),
        }
        return (json.dumps(body) + "\n").encode("utf-8")

    def human(self) -> str:
        """The same, for the person watching stderr."""
        return (
            f"session  {self.session_id}\n"
            f"log      {self.log}\n"
            "input    this process's stdin — one JSON command per line\n"
            f"stop     close stdin (finish and exit) · Ctrl-C or kill -TERM {self.holder_pid} (abort)\n"
        )


__all__ = ["HEADLESS_V1", "SessionHeader"]
