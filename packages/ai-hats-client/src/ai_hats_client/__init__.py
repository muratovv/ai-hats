"""Drive an ``ai-hats headless`` session through its pipes, knowing nothing but its contract."""

from .session import (
    Event,
    Exit,
    Header,
    HeadlessError,
    HeadlessSession,
    HeadlessTimeout,
    OnQuestion,
    ProtocolError,
    QuestionPending,
    SessionEnded,
    Turn,
    answer_command,
    prompt_command,
)

__all__ = [
    "Event",
    "Exit",
    "Header",
    "HeadlessError",
    "HeadlessSession",
    "HeadlessTimeout",
    "OnQuestion",
    "ProtocolError",
    "QuestionPending",
    "SessionEnded",
    "Turn",
    "answer_command",
    "prompt_command",
]
