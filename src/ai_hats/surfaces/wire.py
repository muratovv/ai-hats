"""A surface's structured stdin/stdout stream — its row of the command table (ADR-0038 D3).

The holder is surface-neutral: it asks the surface which flags put the binary on
the wire, how a turn is written, and which line closes one. Nothing else about
the surface's vocabulary reaches it.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ai_hats_observe.canonical import TurnEnded


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

    def turn_end(self, message: Mapping[str, Any]) -> TurnEnded | None:
        """The turn's end when ``message`` is the line that closes one, else ``None``."""
        ...


__all__ = ["Wire"]
