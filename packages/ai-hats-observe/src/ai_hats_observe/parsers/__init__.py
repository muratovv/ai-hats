"""Transcript parsers (HATS-948, T15) — the surface-agnostic parse adapter.

``TranscriptParser`` (contract) + concrete parsers. ``AuditWriter`` consumes the
``ParsedTranscript`` shape only; providers carry the parser (a new surface adds a
parser here, not a branch in the writer). All lightweight — eager re-export.
"""

from __future__ import annotations

from .base import ParsedTranscript, TranscriptParser, Turn
from .claude import ClaudeParser
from .trace import TraceEntry, TraceParser

__all__ = [
    "ClaudeParser",
    "ParsedTranscript",
    "TraceEntry",
    "TraceParser",
    "TranscriptParser",
    "Turn",
]
