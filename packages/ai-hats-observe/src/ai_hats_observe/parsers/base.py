"""Transcript-parse contract (HATS-948, T15) — the surface-agnostic boundary.

``TranscriptParser`` turns a provider's session record (Claude JSONL and/or the
trace.log fallback) into a ``ParsedTranscript``. ``AuditWriter`` consumes ONLY
this shape, so provider-specific parsing stays out of the writer and a new
surface adds a parser, not a branch in the writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from ..canonical.signals import Signal
from ..canonical.views import Response


@dataclass
class Turn:
    timestamp: str
    user_input: str | None = None
    tools: list[str] = field(default_factory=list)
    response: str = ""
    # How long the surface says the model reasoned, where it reports a duration
    # (the trace chrome does). Never derived from the text below — a character
    # count presented as seconds is a fabricated measurement.
    thinking_secs: int = 0
    # The reasoning itself, kept verbatim where the surface emits it.
    thinking: list[str] = field(default_factory=list)


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

    ``model_stats``/``agg_usage`` carry token telemetry only where the surface
    emits it; a parse without it leaves them empty/zero — structured turns or
    not — and says so in ``flags``. Read those before treating a zero as measured.

    ``flags`` says how well we could read the record; ``signals`` says what
    happened to the run. Separate axes: a run killed by the platform is
    perfectly readable, and without the second one it reads as a short clean run.
    """

    turns: list[Turn]
    model_stats: dict[str, dict] = field(default_factory=dict)
    agg_usage: dict[str, int] = field(default_factory=_empty_agg_usage)
    flags: list[str] = field(default_factory=list)
    # One entry per inference call, cost counted once — the canonical record the
    # legacy fields above are derived from. Empty on a surface not yet reading
    # its record as canonical events.
    responses: list[Response] = field(default_factory=list)
    # Run health: auth, quota, service, model switches, unmodelled records.
    signals: list[Signal] = field(default_factory=list)


@runtime_checkable
class TranscriptParser(Protocol):
    """Parse a session record into a ``ParsedTranscript``.

    ``jsonl_path`` is the provider's structured log when it has one (else
    ``None``); ``trace_path`` is observe's own ``trace.log``. A parser decides
    which surface it reads.
    """

    def parse(
        self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path
    ) -> ParsedTranscript: ...

    def parse_usage(
        self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path
    ) -> dict[str, Any]:
        """Build this surface's ``usage/v1`` report (context-cost + timeline).

        A distinct, richer parse than ``parse`` — a surface with a structured
        log populates it; a trace-only surface returns a well-formed empty
        report (no token telemetry).
        """
        ...
