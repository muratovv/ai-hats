"""Claude Code transcript parser (HATS-948, T15).

Structured parse of the ``claude`` binary's JSONL session log when present, else
the trace-chrome fallback (delegated to ``TraceParser``). Owns every Claude-JSONL
assumption (field names, block types), keeping ``AuditWriter`` surface-agnostic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .base import ParsedTranscript, Turn
from .trace import TraceParser

logger = logging.getLogger(__name__)


class ClaudeParser:
    """Parse a Claude session record into a ``ParsedTranscript``.

    JSONL present → structured turns + token telemetry; else → the trace-log
    fallback (``TraceParser``), which carries no token data.
    """

    def __init__(self) -> None:
        self._trace = TraceParser()

    def parse(
        self, jsonl_path: Path | None, trace_path: Path
    ) -> ParsedTranscript:
        if jsonl_path and jsonl_path.exists():
            turns, model_stats, agg_usage = self._parse_jsonl(jsonl_path)
            return ParsedTranscript(
                turns=turns, model_stats=model_stats, agg_usage=agg_usage
            )
        if jsonl_path:
            logger.debug("JSONL not found at %s — falling back to trace", jsonl_path)
        return self._trace.parse(None, trace_path)

    def _parse_jsonl(self, jsonl_path: Path) -> tuple[list[Turn], dict[str, dict], dict]:
        """Parse Claude Code JSONL → (turns, per-model stats, aggregated usage)."""
        turns: list[Turn] = []
        current: Turn | None = None
        model_stats: dict[str, dict] = {}
        agg_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        prev_model: str | None = None

        for line in jsonl_path.read_text().splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            ts = obj.get("timestamp", "")[:19]
            message = obj.get("message", {})
            content = message.get("content", [])

            if msg_type == "user":
                user_text = self._extract_user_text(content)
                if user_text:
                    current = Turn(timestamp=ts, user_input=user_text)
                    turns.append(current)

            elif msg_type == "assistant" and current is not None:
                model = message.get("model", "unknown")
                usage = message.get("usage", {})
                tok_in = usage.get("input_tokens", 0)
                tok_out = usage.get("output_tokens", 0)

                if model not in model_stats:
                    model_stats[model] = {"in": 0, "out": 0, "calls": 0}
                model_stats[model]["in"] += tok_in
                model_stats[model]["out"] += tok_out
                model_stats[model]["calls"] += 1

                agg_usage["input_tokens"] += tok_in
                agg_usage["output_tokens"] += tok_out
                agg_usage["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
                agg_usage["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)

                # Track model switches within turns
                if prev_model and model != prev_model:
                    current.tools.append(f"⚙️ Model: {model}")
                prev_model = model

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type")
                        if bt == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                current.thinking_secs = max(1, len(thinking) // 200)
                            else:
                                current.thinking_secs = max(current.thinking_secs, 1)
                        elif bt == "tool_use":
                            name = block.get("name", "?")
                            inp = block.get("input", {})
                            summary = self._summarize_tool_input(name, inp)
                            current.tools.append(f"{name}: {summary}")
                        elif bt == "text":
                            text = block.get("text", "").strip()
                            if text:
                                current.response = text

        return turns, model_stats, agg_usage

    @staticmethod
    def _extract_user_text(content) -> str | None:
        """Extract user text from message content, filtering system/command messages."""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            has_tool_result = any(
                isinstance(c, dict) and c.get("type") == "tool_result" for c in content
            )
            if has_tool_result:
                return None
            parts = [
                c["text"] for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            text = " ".join(parts).strip()
        else:
            return None

        if not text:
            return None
        # Filter Claude Code system messages
        if text.startswith(("<", "/")):
            return None
        # HATS-666: a Skill invocation re-injects the full SKILL.md as a user
        # text message ("Base directory for this skill: <path>"). That body is
        # 100% redundant with the `🔧 Skill: <name>` tool line the audit already
        # renders — filter it like a tool_result so it never becomes a 👤 turn.
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
