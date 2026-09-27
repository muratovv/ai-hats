"""A surface's structured stdin/stdout stream — its codec for the command table (ADR-0038 D3).

The holder is surface-neutral: it asks the surface which flags put the binary on
the wire, how a command is written, and what each stdout line means — canonical
events, or a question the binary is waiting on. Nothing else about the
surface's vocabulary reaches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ai_hats_observe.canonical import Event, ToolCallId
from ai_hats_observe.commands import Answer, Interrupt, Prompt


@dataclass(frozen=True)
class Question:
    """The binary waits on a person before the call ``call_id`` runs.

    ``request_id`` and ``input`` are what the reply needs; they live only in the
    holder's memory (ADR-0038 D3). ``input`` is the call as it will run, with
    whatever a hook put on it."""

    request_id: str
    call_id: ToolCallId
    tool: str
    input: Mapping[str, Any] = field(default_factory=dict)
    # the gate's own words, when a gate asked
    reason: str | None = None
    # who put the question: the ``source`` of the PersonAsked the holder writes for it
    source: str | None = None
    # the model's own question, answered with ``answers`` rather than a bare decision
    takes_answers: bool = False


@dataclass(frozen=True)
class Withdrawn:
    """The binary took a question back itself, as it does on an interrupt."""

    request_id: str


Control = Question | Withdrawn


class WireDecoder(Protocol):
    """One session's stdout, line by line, as the main agent's events (ADR-0038 D4)."""

    #: The binary's own session id, as its stdout last named it; ``None`` until it does.
    #: It can change mid-run — claude's ``/clear`` goes on in a new session and transcript.
    provider_session_id: str | None

    def sent(self, prompt: Prompt) -> None:
        """The holder is writing ``prompt`` to the binary's stdin; called before the write.

        Its receipt is then the decoder's to announce: one ``PromptReceived`` with
        ``origin`` ``person`` and the text as sent, before any event of the turn
        that answers it."""
        ...

    def decode(self, line: Mapping[str, Any]) -> list[Event]:
        """The events one stdout line says; a sub-agent's line and a question say none."""
        ...

    def control(self, line: Mapping[str, Any]) -> Control | None:
        """The question a stdout line puts, or takes back; ``None`` for any other line."""
        ...

    def close(self) -> list[Event]:
        """The stream ended: what it still held open, such as a cut response."""
        ...


class Wire(Protocol):
    """How one surface's binary is driven over pipes."""

    #: Appended to the interactive argv to put the binary on the wire.
    launch_args: tuple[str, ...]

    def owned_in(self, args: list[str]) -> list[str]:
        """The flags among ``args`` the holder sets or forbids itself, in order."""
        ...

    def encode(self, command: Prompt | Interrupt) -> bytes:
        """One command as the binary reads it from its stdin: a single line.
        The prompt's id becomes the surface's own id for it, so the wire says
        it back when the turn starts and ends."""
        ...

    def reply(self, question: Question, answer: Answer) -> bytes:
        """The answer to ``question`` as the binary reads it: a single line."""
        ...

    def decoder(self) -> WireDecoder:
        """A fresh reader for one session's stdout."""
        ...


__all__ = ["Control", "Question", "Wire", "WireDecoder", "Withdrawn"]
