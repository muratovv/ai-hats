"""Transcript-parse contract (HATS-948, T15) — the surface-agnostic boundary.

``TranscriptParser`` turns a provider's session record (Claude JSONL and/or the
trace.log fallback) into a ``ParsedTranscript``. ``AuditWriter`` consumes ONLY
this shape, so provider-specific parsing stays out of the writer and a new
surface adds a parser, not a branch in the writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Turn:
    timestamp: str
    user_input: str | None = None
    tools: list[str] = field(default_factory=list)
    response: str = ""
    thinking_secs: int = 0


def _empty_agg_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


@dataclass(frozen=True)
class ParsedTranscript:
    """A parsed session: turns + optional token telemetry.

    ``model_stats``/``agg_usage`` are populated only by structured (JSONL) parses;
    a trace-only parse leaves them empty/zero (no token data on that surface).
    """

    turns: list[Turn]
    model_stats: dict[str, dict] = field(default_factory=dict)
    agg_usage: dict[str, int] = field(default_factory=_empty_agg_usage)


@runtime_checkable
class TranscriptParser(Protocol):
    """Parse a session record into a ``ParsedTranscript``.

    ``jsonl_path`` is the provider's structured log when it has one (else
    ``None``); ``trace_path`` is observe's own ``trace.log``. A parser decides
    which surface it reads.
    """

    def parse(
        self, jsonl_path: Path | None, trace_path: Path
    ) -> ParsedTranscript: ...

    def parse_usage(
        self, jsonl_path: Path | None, trace_path: Path
    ) -> dict[str, Any]:
        """Build this surface's ``usage/v1`` report (context-cost + timeline).

        A distinct, richer parse than ``parse`` — a surface with a structured
        log populates it; a trace-only surface returns a well-formed empty
        report (no token telemetry).
        """
        ...
