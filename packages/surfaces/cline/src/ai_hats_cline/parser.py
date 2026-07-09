"""Cline transcript parser (HATS-960, Adapter B).

Parses cline's single-object ``<id>.messages.json`` into observe's
surface-agnostic ``ParsedTranscript`` + ``usage/v1``. A cline-field port of
``ClaudeParser``; the field/token-key mapping is grounded in the HATS-960 card
(R2, verified cline v3.0.3).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_hats_observe.parsers.base import ParsedTranscript, Turn
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.usage import empty_usage_report

logger = logging.getLogger(__name__)

# A reference Read loads skill-body depth (mirrors ai_hats_observe.usage).
_REF_MARKERS = ("/references/",)


def _is_reference_path(file_path: str) -> bool:
    if not file_path:
        return False
    return file_path.endswith("SKILL.md") or any(m in file_path for m in _REF_MARKERS)


def _iso(ts: Any) -> str:
    """cline ``ts`` (epoch-ms int) → ``YYYY-MM-DDTHH:MM:SS`` UTC (Claude's ``[:19]``)."""
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat()[:19]
    except (TypeError, ValueError):
        return ""


class ClineParser:
    """Parse a cline ``.messages.json`` into a ``ParsedTranscript`` / ``usage/v1``.

    ``messages_path`` present → structured turns + token telemetry; else the
    trace-log fallback (``TraceParser``), which carries no token data.
    """

    def __init__(self) -> None:
        self._trace = TraceParser()

    # -- parse -> ParsedTranscript ------------------------------------------

    def parse(self, jsonl_path: Path | None, trace_path: Path) -> ParsedTranscript:
        doc = self._load(jsonl_path)
        if doc is None:
            if jsonl_path:
                logger.debug("cline messages.json unusable at %s — trace fallback", jsonl_path)
            return self._trace.parse(None, trace_path)
        turns, model_stats, agg_usage = self._parse_messages(doc.get("messages", []))
        return ParsedTranscript(turns=turns, model_stats=model_stats, agg_usage=agg_usage)

    def parse_usage(self, jsonl_path: Path | None, trace_path: Path) -> dict[str, Any]:
        """cline ``.messages.json`` present → the measured ``usage/v1`` report;
        else the trace fallback (no token telemetry)."""
        doc = self._load(jsonl_path)
        if doc is None:
            return self._trace.parse_usage(None, trace_path)

        report = empty_usage_report(Path(jsonl_path).name)  # type: ignore[arg-type]
        report["session_id"] = doc.get("sessionId")
        types_seen = report["entry_types_seen"]
        totals = report["usage_totals"]
        agg = report["aggregates"]
        timeline = report["timeline"]
        pending: list[dict[str, Any]] = []

        for msg in doc.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            types_seen[role] = types_seen.get(role, 0) + 1
            ts = _iso(msg.get("ts"))
            if role == "assistant":
                self._usage_assistant(msg, report, totals, agg, timeline, pending, ts)
            elif role == "user":
                self._usage_user(msg, agg)

        results = agg["tool_results"]
        if results:
            # cline's tool_result carries no error marker (verified) → errors are
            # not derivable; success-rate is a no-error-signal 1.0, flagged so a
            # consumer never reads it as measured.
            agg["tool_success_rate"] = round(1.0 - agg["tool_errors"] / results, 4)
            report["flags"].append(
                "tool-errors-not-derivable: cline tool_result carries no error marker"
            )
        return report

    # -- internals ----------------------------------------------------------

    def _load(self, path: Path | None) -> dict[str, Any] | None:
        """Read the single-object ``.messages.json`` → dict, or None (absent /
        unreadable / malformed / not an object) so the caller falls back to trace."""
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return obj if isinstance(obj, dict) else None

    def _parse_messages(
        self, messages: list
    ) -> tuple[list[Turn], dict[str, dict], dict]:
        """Parse cline ``messages[]`` → (turns, per-model stats, aggregated usage)."""
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

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            ts = _iso(msg.get("ts"))
            content = msg.get("content", [])

            if role == "user":
                user_text = self._extract_user_text(content)
                if user_text:
                    current = Turn(timestamp=ts, user_input=user_text)
                    turns.append(current)

            elif role == "assistant" and current is not None:
                model = (msg.get("modelInfo") or {}).get("id", "unknown")
                metrics = msg.get("metrics") or {}
                tok_in = int(metrics.get("inputTokens", 0) or 0)
                tok_out = int(metrics.get("outputTokens", 0) or 0)

                if model not in model_stats:
                    model_stats[model] = {"in": 0, "out": 0, "calls": 0}
                model_stats[model]["in"] += tok_in
                model_stats[model]["out"] += tok_out
                model_stats[model]["calls"] += 1

                agg_usage["input_tokens"] += tok_in
                agg_usage["output_tokens"] += tok_out
                agg_usage["cache_read_input_tokens"] += int(metrics.get("cacheReadTokens", 0) or 0)
                agg_usage["cache_creation_input_tokens"] += int(
                    metrics.get("cacheWriteTokens", 0) or 0
                )

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

    # -- usage/v1 walkers (cline-field analogues of ai_hats_observe.usage) ---

    @staticmethod
    def _usage_assistant(
        msg: dict[str, Any],
        report: dict[str, Any],
        totals: dict[str, int],
        agg: dict[str, Any],
        timeline: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        ts: str,
    ) -> None:
        metrics = msg.get("metrics") or {}
        cache_creation = int(metrics.get("cacheWriteTokens", 0) or 0)

        if report["always_on"] is None and metrics:
            report["always_on"] = {
                "first_input_tokens": int(metrics.get("inputTokens", 0) or 0),
                "first_cache_creation_input_tokens": cache_creation,
                "first_cache_read_input_tokens": int(metrics.get("cacheReadTokens", 0) or 0),
                "model": (msg.get("modelInfo") or {}).get("id"),
                "note": (
                    "measured proxy: initial cached working set "
                    "(system prompt + first user turn); not role-attributed"
                ),
            }

        totals["input_tokens"] += int(metrics.get("inputTokens", 0) or 0)
        totals["output_tokens"] += int(metrics.get("outputTokens", 0) or 0)
        totals["cache_read_input_tokens"] += int(metrics.get("cacheReadTokens", 0) or 0)
        totals["cache_creation_input_tokens"] += cache_creation

        # Reconstructed attribution (FIFO): the oldest pending load event gets
        # this turn's freshly-cached tokens. Events that never see a following
        # cache_creation keep tokens_delta = None (never a magic 0).
        if cache_creation and pending:
            ev = pending.pop(0)
            ev["tokens_delta"] = cache_creation
            ev["tokens_attribution"] = "reconstructed"

        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            agg["tool_calls"] += 1
            name = block.get("name", "?")
            inp = block.get("input") or {}
            if name == "Skill":
                skill = inp.get("skill", "?")
                agg["skill_loads"][skill] = agg["skill_loads"].get(skill, 0) + 1
                ev = {"ts": ts, "kind": "skill_load", "name": skill,
                      "tokens_delta": None, "args": inp.get("args")}
                timeline.append(ev)
                pending.append(ev)
            elif name == "Read" and _is_reference_path(inp.get("file_path", "")):
                fp = inp.get("file_path", "")
                agg["reference_reads"][fp] = agg["reference_reads"].get(fp, 0) + 1
                ev = {"ts": ts, "kind": "reference_read", "name": fp, "tokens_delta": None}
                timeline.append(ev)
                pending.append(ev)
            else:
                timeline.append({"ts": ts, "kind": "tool", "name": name})

    @staticmethod
    def _usage_user(msg: dict[str, Any], agg: dict[str, Any]) -> None:
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                agg["tool_results"] += 1
                # No error marker on cline tool_result → errors stay 0 (flagged).

    @staticmethod
    def _extract_user_text(content) -> str | None:
        """User text, filtering tool-result carriers and system-ish messages
        (mirrors ``ClaudeParser._extract_user_text``)."""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
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
        if text.startswith(("<", "/")):
            return None
        if text.startswith("Base directory for this skill:"):
            return None
        return text

    @staticmethod
    def _summarize_tool_input(name: str, inp: dict) -> str:
        """Short one-line summary of a tool_use input (mirrors ClaudeParser)."""
        if name == "Bash":
            return inp.get("command", inp.get("description", ""))[:100]
        if name in ("Read", "Write", "Edit"):
            return inp.get("file_path", "")
        if name in ("Grep", "Glob"):
            return inp.get("pattern", "")
        if name == "Skill":
            return inp.get("skill", "")
        for v in inp.values():
            if isinstance(v, str) and v:
                return v[:80]
        return str(inp)[:80]
