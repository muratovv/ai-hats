"""Kimi wire.jsonl parser — the surface's event-sourced session record.

Kimi appends one JSON event per line: ``turn.prompt`` opens a turn,
``context.append_loop_event`` carries the loop (content.part text/think,
tool.call/result, step.end), ``usage.record`` reports per-model tokens per
scope, and ``metadata.protocol_version`` is the churn anchor — an unknown
version or event type surfaces as a flag + UNSUPPORTED_RECORD signal, never a
silent misread and never an exception (a crashing parser would kill the whole
audit write).
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
# Events the parser reads or deliberately ignores — anything else is drift.
KNOWN_EVENTS = {
    "metadata",
    "config.update",
    "turn.prompt",
    "context.append_message",
    "context.append_loop_event",
    "llm.request",
    "llm.tools_snapshot",
    "tools.set_active_tools",
    "permission.set_mode",
    "usage.record",
}

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
        unknown: set[str] = set()

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
                etype = event.get("type")
                if etype == "metadata":
                    # "1.4" — the major is the breaking-change anchor.
                    major = str(event.get("protocol_version", "")).split(".", 1)[0]
                    if not major.isdigit() or int(major) not in KNOWN_PROTOCOL_MAJORS:
                        drift(f"unknown wire protocol_version: {event.get('protocol_version')!r}")
                    continue
                if etype not in KNOWN_EVENTS:
                    unknown.add(str(etype))
                    continue
                if etype == "turn.prompt":
                    text = " ".join(
                        str(part.get("text", ""))
                        for part in event.get("input") or []
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                    turns.append(Turn(timestamp=_ts(event.get("time")), user_input=text))
                elif etype == "context.append_loop_event":
                    loop = event.get("event") or {}
                    kind = loop.get("type")
                    if kind == "content.part" and turns:
                        part = loop.get("part") or {}
                        if part.get("type") == "text":
                            turns[-1].response += str(part.get("text", ""))
                        elif part.get("type") == "think":
                            turns[-1].thinking.append(str(part.get("think", "")))
                    elif kind == "tool.call" and turns:
                        turns[-1].tools.append(str(loop.get("name", "")))
                elif etype == "usage.record":
                    model = str(event.get("model") or "unknown")
                    row = stats.setdefault(model, {"in": 0, "out": 0, "calls": 0})
                    usage = event.get("usage") or {}
                    row["calls"] += 1
                    row["in"] += int(usage.get("inputOther", 0) or 0)
                    row["out"] += int(usage.get("output", 0) or 0)
                    for kimi_key, canonical in _USAGE_MAP.items():
                        agg[canonical] += int(usage.get(kimi_key, 0) or 0)

        for etype in sorted(unknown):
            drift(f"unknown wire event type: {etype}")

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
