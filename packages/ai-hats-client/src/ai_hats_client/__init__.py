"""Drive an ``ai-hats headless`` session through its pipes, knowing nothing but its contract."""

from .session import (
    Event,
    Exit,
    Header,
    HeadlessError,
    HeadlessSession,
    HeadlessTimeout,
    ProtocolError,
    SessionEnded,
    Turn,
    prompt_command,
)

__all__ = [
    "Event",
    "Exit",
    "Header",
    "HeadlessError",
    "HeadlessSession",
    "HeadlessTimeout",
    "ProtocolError",
    "SessionEnded",
    "Turn",
    "prompt_command",
]
