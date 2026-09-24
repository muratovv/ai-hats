"""A surface's structured stdin/stdout stream — its codec for the command table (ADR-0038 D3).

The holder is surface-neutral: it asks the surface which flags put the binary on
the wire, how a turn is written, and what each stdout line means as canonical
events. Nothing else about the surface's vocabulary reaches it.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ai_hats_observe.canonical import Event


class WireDecoder(Protocol):
    """One session's stdout, line by line, as the main agent's events (ADR-0038 D4)."""

    def decode(self, line: Mapping[str, Any]) -> list[Event]:
        """The events one stdout line says; a sub-agent's line says none."""
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

    def prompt_line(self, text: str) -> bytes:
        """One turn, as the binary reads it from its stdin: a single line."""
        ...

    def decoder(self) -> WireDecoder:
        """A fresh reader for one session's stdout."""
        ...


__all__ = ["Wire", "WireDecoder"]
