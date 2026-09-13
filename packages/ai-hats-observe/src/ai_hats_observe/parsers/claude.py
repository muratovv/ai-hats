"""Claude Code transcript parser (HATS-948, T15; HATS-1966).

Structured parse of the ``claude`` binary's JSONL session log when present, else
the trace-chrome fallback (delegated to ``TraceParser``). The JSONL itself is
read by ``ClaudeTranscriptReader``, which owns every Claude-JSONL assumption and
yields canonical events; this module derives the legacy ``ParsedTranscript``
shape from them, keeping ``AuditWriter`` surface-agnostic.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import usage as _usage
from ..canonical.events import (
    Event,
    ItemEmitted,
    PromptReceived,
    ResponseStarted,
    ToolResultReceived,
)
from ..canonical.types import (
    Item,
    TextItem,
    ThinkingItem,
    ToolCallId,
    ToolCallItem,
    Usage,
)
from ..canonical.views import Response, collect
from .base import ParsedTranscript, Turn
from .claude_events import ClaudeTranscriptReader
from .trace import TraceParser

logger = logging.getLogger(__name__)

#: How much of a failed tool result reaches the audit line. The message is the
#: point; the payload that follows it belongs to the transcript.
ERROR_EXCERPT = 200


class ClaudeParser:
    """Parse a Claude session record into a ``ParsedTranscript``.

    JSONL present → structured turns + token telemetry; else → the trace-log
    fallback (``TraceParser``), which carries no token data.
    """

    def __init__(self) -> None:
        self._trace = TraceParser()

    @staticmethod
    def _normalize_paths(jsonl_path: Path | Iterable[Path] | None) -> list[Path]:
        if jsonl_path is None:
            return []
        if isinstance(jsonl_path, (Path, str)):
            p = Path(jsonl_path)
            return [p] if p.exists() else []
        return [Path(p) for p in jsonl_path if Path(p).exists()]

    def parse(self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path) -> ParsedTranscript:
        paths = self._normalize_paths(jsonl_path)
        if paths:
            return self._parse_jsonl(paths)
        if jsonl_path:
            logger.debug("JSONL not found at %s — falling back to trace", jsonl_path)
        return self._trace.parse(None, trace_path)

    def parse_usage(
        self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path
    ) -> dict[str, Any]:
        """JSONL present → the measured ``usage/v1`` report; else trace fallback."""
        paths = self._normalize_paths(jsonl_path)
        if paths:
            # If multiple paths exist, parse the primary (first) transcript
            return _usage.parse_session_usage(paths[0])
        return self._trace.parse_usage(None, trace_path)

    def _parse_jsonl(self, jsonl_paths: Path | Iterable[Path]) -> ParsedTranscript:
        """Read the transcript(s) as canonical events; derive the rest from them.

        Everything the legacy shape reports is a projection of one event stream,
        so ``turns`` and the token counters can no longer disagree about what the
        session contained.
        """
        paths = (
            [Path(jsonl_paths)]
            if isinstance(jsonl_paths, (Path, str))
            else [Path(p) for p in jsonl_paths]
        )
        events = [event for path in paths for event in ClaudeTranscriptReader(path).read()]
        collected = collect(events)
        responses = _once_per_call(collected.responses)
        return ParsedTranscript(
            turns=self._turns(events),
            model_stats=_model_stats(responses),
            agg_usage=_agg_usage(responses),
            responses=responses,
            signals=collected.signals,
        )

    # -- the legacy turn shape, derived ------------------------------------

    @classmethod
    def _turns(cls, events: Iterable[Event]) -> list[Turn]:
        """Fold the stream into turns: what a person asked, and what followed.

        The reader reports every prompt the surface carries, including the
        harness's own injections; which of them reads as a turn is this audit's
        question, not the record's.
        """
        turns: list[Turn] = []
        current: Turn | None = None
        prev_model: str | None = None
        # where each call's line sits, so its outcome can land on that line
        placed: dict[ToolCallId, tuple[Turn, int]] = {}

        for event in events:
            match event:
                case PromptReceived():
                    text = cls._displayable_prompt(event.text)
                    if text:
                        current = Turn(timestamp=(event.ts or "")[:19], user_input=text)
                        turns.append(current)
                case ResponseStarted() if event.model:
                    if current is not None and prev_model and event.model != prev_model:
                        current.tools.append(f"⚙️ Model: {event.model}")
                    prev_model = event.model
                case ItemEmitted() if current is not None:
                    cls._place_item(current, event.item, placed)
                case ToolResultReceived():
                    cls._record_outcome(event, placed)
        return turns

    @classmethod
    def _place_item(
        cls, turn: Turn, item: Item, placed: dict[ToolCallId, tuple[Turn, int]]
    ) -> None:
        match item:
            case TextItem():
                text = item.text.strip()
                if text:
                    # Accumulated, never overwritten: the last text-bearing
                    # fragment used to win, and 45.3% of turns lost the rest.
                    turn.response = f"{turn.response}\n\n{text}" if turn.response else text
            case ThinkingItem():
                text = "[redacted thinking]" if item.redacted else item.text.strip()
                if text:
                    turn.thinking.append(text)
            case ToolCallItem():
                summary = cls._summarize_tool_input(item.name, item.input)
                turn.tools.append(f"{item.name}: {summary}")
                placed[item.call_id] = (turn, len(turn.tools) - 1)

    @staticmethod
    def _record_outcome(
        result: ToolResultReceived, placed: dict[ToolCallId, tuple[Turn, int]]
    ) -> None:
        """Mark the call's line with what came back.

        A line with no mark is a call whose outcome this record never reported —
        which is a different thing from one that succeeded.
        """
        where = placed.get(result.call_id)
        if where is None:
            return
        turn, index = where
        if result.ok:
            turn.tools[index] = f"{turn.tools[index]} ✓"
            return
        turn.tools[index] = f"{turn.tools[index]} ✗ {_result_excerpt(result.content)}".rstrip()

    # -- text -------------------------------------------------------------

    @staticmethod
    def _displayable_prompt(text: str) -> str | None:
        """The prompt as a turn opener, or ``None`` for harness chatter."""
        text = text.strip()
        if not text:
            return None
        # Claude Code system messages and slash commands.
        if text.startswith(("<", "/")):
            return None
        # HATS-666: a Skill invocation re-injects the whole SKILL.md as a user
        # message, 100% redundant with the `🔧 Skill: <name>` tool line already
        # rendered — filtered like a tool_result, never a 👤 turn of its own.
        if text.startswith("Base directory for this skill:"):
            return None
        return text

    @staticmethod
    def _summarize_tool_input(name: str, inp: dict) -> str:
        """Summarize tool input to a short string."""
        if name == "Bash":
            return inp.get("command", inp.get("description", ""))[:100]
        if name in ("Read", "Write", "Edit"):
            return inp.get("file_path", "")
        if name in ("Grep", "Glob"):
            return inp.get("pattern", "")
        if name == "Agent":
            return inp.get("description", inp.get("prompt", ""))[:80]
        # Generic: show first string value
        for v in inp.values():
            if isinstance(v, str) and v:
                return v[:80]
        return str(inp)[:80]


# --- projections over the collected run ------------------------------------


def _once_per_call(responses: list[Response]) -> list[Response]:
    """One entry per inference call.

    Several records of one session (a resumed or rotated transcript) repeat a
    call verbatim, and billing it twice is the defect this parser exists to fix.
    """
    seen: set[str] = set()
    unique: list[Response] = []
    for response in responses:
        if response.response_id in seen:
            continue
        seen.add(response.response_id)
        unique.append(response)
    return unique


def _model_stats(responses: list[Response]) -> dict[str, dict]:
    """Per model: calls and tokens. ``calls`` counts API calls, not fragments."""
    stats: dict[str, dict] = {}
    for response in responses:
        row = stats.setdefault(str(response.model or "unknown"), {"in": 0, "out": 0, "calls": 0})
        row["in"] += response.usage.input_tokens
        row["out"] += response.usage.output_tokens
        row["calls"] += 1
    return stats


def _agg_usage(responses: list[Response]) -> dict[str, int]:
    total = Usage()
    for response in responses:
        total = total + response.usage
    return {
        "input_tokens": total.input_tokens,
        "output_tokens": total.output_tokens,
        "cache_read_input_tokens": total.cache_read_input_tokens,
        "cache_creation_input_tokens": total.cache_creation_input_tokens,
    }


def _result_excerpt(content: Any) -> str:
    """The failure message, flattened to one line the audit can carry."""
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part)
    elif content is None:
        text = ""
    else:
        text = str(content)
    flat = " ".join(text.split())
    return f"{flat[:ERROR_EXCERPT]} …" if len(flat) > ERROR_EXCERPT else flat
