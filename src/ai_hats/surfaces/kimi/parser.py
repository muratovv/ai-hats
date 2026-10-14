"""Kimi wire.jsonl parser — the surface's event-sourced session record.

Reads ``turn.prompt`` / ``context.append_loop_event`` / ``usage.record`` /
``metadata.protocol_version``. An unknown protocol major, unparsable line, or
a shape change in a PARSED event is a flag + UNSUPPORTED_RECORD signal, never
an exception; an unknown top-level type is additive evolution and passes
silently — a full session emits dozens of types this parser never reads.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ai_hats_observe.parsers.base import ParsedTranscript, Turn
from ai_hats_observe.parsers.trace import TraceParser

logger = logging.getLogger(__name__)

KNOWN_PROTOCOL_MAJORS = {1}

_USAGE_MAP = {
    "inputOther": "input_tokens",
    "output": "output_tokens",
    "inputCacheRead": "cache_read_input_tokens",
    "inputCacheCreation": "cache_creation_input_tokens",
}


def _ts(epoch_ms: object) -> str:
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


class KimiParser:
    """``TranscriptParser`` over kimi's ``agents/main/wire.jsonl``."""

    def __init__(self, *, trace: TraceParser | None = None) -> None:
        self._trace = trace or TraceParser()

    def parse(self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path) -> ParsedTranscript:
        from ai_hats_observe.canonical.signals import Notice, WorthRecording
        from ai_hats_observe.canonical.types import now

        paths = self._normalize(jsonl_path)
        if not paths:
            return self._trace.parse(None, trace_path)

        turns: list[Turn] = []
        stats: dict[str, dict] = {}
        agg = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        flags: list[str] = []
        signals: list[Notice] = []

        def drift(detail: str) -> None:
            flags.append("schema-drift")
            signals.append(
                Notice(
                    reason=WorthRecording.UNSUPPORTED_RECORD,
                    detail=detail,
                    source="kimi/wire",
                    ts=now(),
                )
            )

        for path in paths:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    drift(f"{path.name}:{lineno}: unparsable line")
                    continue
                if not isinstance(event, dict):
                    drift(f"{path.name}:{lineno}: record is not an object")
                    continue
                etype = event.get("type")
                if etype == "metadata":
                    # "1.4" — the major is the breaking-change anchor.
                    major = str(event.get("protocol_version", "")).split(".", 1)[0]
                    if not major.isdigit() or int(major) not in KNOWN_PROTOCOL_MAJORS:
                        drift(f"unknown wire protocol_version: {event.get('protocol_version')!r}")
                    continue
                # Unknown types are additive evolution, not drift: a full
                # session emits dozens of types the parser never reads. Drift
                # is a SHAPE change in an event the parser depends on.
                if etype == "turn.prompt":
                    if not isinstance(event.get("input"), list):
                        drift("turn.prompt carries no input list")
                        continue
                    text = " ".join(
                        str(part.get("text", ""))
                        for part in event.get("input") or []
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                    turns.append(Turn(timestamp=_ts(event.get("time")), user_input=text))
                elif etype == "context.append_loop_event":
                    loop = event.get("event")
                    if not isinstance(loop, dict):
                        drift("context.append_loop_event carries no event object")
                        continue
                    kind = loop.get("type")
                    if kind == "content.part" and turns:
                        part = loop.get("part")
                        if not isinstance(part, dict):
                            drift("content.part carries no part object")
                            continue
                        if part.get("type") == "text":
                            turns[-1].response += str(part.get("text", ""))
                        elif part.get("type") == "think":
                            turns[-1].thinking.append(str(part.get("think", "")))
                    elif kind == "tool.call" and turns:
                        turns[-1].tools.append(str(loop.get("name", "")))
                elif etype == "usage.record":
                    usage = event.get("usage")
                    if not isinstance(usage, dict):
                        drift("usage.record carries no usage object")
                        continue
                    try:
                        amounts = {
                            key: int(usage.get(key, 0) or 0)
                            for key in ("inputOther", "output", *_USAGE_MAP)
                        }
                    except (TypeError, ValueError):
                        drift("usage.record carries a non-numeric usage value")
                        continue
                    model = str(event.get("model") or "unknown")
                    row = stats.setdefault(model, {"in": 0, "out": 0, "calls": 0})
                    row["calls"] += 1
                    row["in"] += amounts["inputOther"]
                    row["out"] += amounts["output"]
                    for kimi_key, canonical in _USAGE_MAP.items():
                        agg[canonical] += amounts[kimi_key]

        parsed = ParsedTranscript(
            turns=turns,
            model_stats=stats,
            agg_usage=agg,
            flags=list(dict.fromkeys(flags)),
            signals=signals,
        )
        if not stats:
            parsed.flags.append("no-token-telemetry")
        return parsed

    def parse_usage(self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path) -> dict:
        """The usage/v1 report builder is claude-event-based; kimi keeps the
        well-formed trace fallback until it speaks canonical events."""
        return self._trace.parse_usage(None, trace_path)

    @staticmethod
    def _normalize(jsonl_path: Path | Iterable[Path] | None) -> list[Path]:
        if jsonl_path is None:
            return []
        if isinstance(jsonl_path, (Path, str)):
            p = Path(jsonl_path)
            return [p] if p.exists() else []
        return [Path(p) for p in jsonl_path if Path(p).exists()]


__all__ = ["KimiParser"]
