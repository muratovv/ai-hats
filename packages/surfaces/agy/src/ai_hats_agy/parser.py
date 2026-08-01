"""Antigravity (agy) transcript parser (HATS-1391).

Parses Antigravity CLI's ``transcript.jsonl`` (located under
``~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl``)
into observe's surface-agnostic ``ParsedTranscript`` + ``usage/v1``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY
from ai_hats_observe.parsers.base import ParsedTranscript, Turn
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.usage import empty_usage_report

logger = logging.getLogger(__name__)


def _clean_user_text(text: str) -> str | None:
    if not text or not isinstance(text, str):
        return None
    if "<USER_REQUEST>" in text:
        parts = text.split("<USER_REQUEST>")
        if len(parts) > 1:
            req_part = parts[1].split("</USER_REQUEST>")[0]
            text = req_part.strip()
    if text.startswith(("<SYSTEM_MESSAGE>", "<SYSTEM_REMINDER>", "<USER_SETTINGS_CHANGE>")):
        return None
    return text.strip() if text.strip() else None


def _summarize_tool_args(name: str, args: dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return str(args)[:80]
    if name == "run_command":
        return str(args.get("CommandLine", ""))[:100]
    if name in ("view_file", "replace_file_content", "multi_replace_file_content", "write_to_file"):
        return str(args.get("AbsolutePath", args.get("TargetFile", "")))
    if name == "grep_search":
        return str(args.get("Query", ""))
    if name == "list_dir":
        return str(args.get("DirectoryPath", ""))
    if name == "read_url_content":
        return str(args.get("Url", ""))
    if name == "invoke_subagent":
        subagents = args.get("Subagents", [])
        if isinstance(subagents, list) and subagents:
            roles = [s.get("Role", s.get("TypeName", "")) for s in subagents if isinstance(s, dict)]
            return ", ".join(roles)[:80]
    for v in args.values():
        if isinstance(v, str) and v:
            return v[:80]
    return str(args)[:80]


import re


def _get_tokens_from_metrics(trace_path: Path) -> dict[str, int] | None:
    session_dir = trace_path.parent
    metrics_file = session_dir / "metrics.json"
    if not metrics_file.is_file():
        return None
    try:
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
        tokens = data.get("tokens")
        if isinstance(tokens, dict) and (tokens.get("input") or tokens.get("output")):
            return {
                "input_tokens": tokens.get("input", 0),
                "output_tokens": tokens.get("output", 0),
                "cache_read_input_tokens": tokens.get("cache_read", 0),
                "cache_creation_input_tokens": tokens.get("cache_creation", 0),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _extract_tokens_from_trace(trace_path: Path) -> dict[str, int] | None:
    if not trace_path.is_file():
        return None
    try:
        text = trace_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    def _parse_num(s: str) -> int:
        s = s.strip().lower()
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))

    thought_matches = re.findall(
        r"Thought for [^,\n]+,\s*([\d\.]+\s*k?)\s+tokens", text, re.IGNORECASE
    )
    ctx_matches = re.findall(r"[↓·]\s*([\d\.]+\s*k?)\s+tokens", text, re.IGNORECASE)
    generic_matches = re.findall(r"([\d\.]+\s*k?)\s+tokens", text, re.IGNORECASE)

    out_toks = sum(_parse_num(m) for m in thought_matches) if thought_matches else 0
    in_toks = max((_parse_num(m) for m in ctx_matches), default=0)

    if not in_toks and not out_toks and generic_matches:
        out_toks = sum(_parse_num(m) for m in generic_matches)

    if in_toks or out_toks:
        return {
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    return None


def _estimate_tokens_from_turns(turns: list[Turn] | None) -> dict[str, int] | None:
    if not turns:
        return None
    from ai_hats.costs import count_tokens_approx

    in_toks = 0
    out_toks = 0
    for t in turns:
        if getattr(t, "user_input", None):
            in_toks += count_tokens_approx(t.user_input)
        if getattr(t, "response", None):
            out_toks += count_tokens_approx(t.response)
        if getattr(t, "tools", None):
            for tool_item in t.tools:
                in_toks += count_tokens_approx(str(tool_item))

    if in_toks or out_toks:
        return {
            "input_tokens": max(in_toks, 1),
            "output_tokens": max(out_toks, 1),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    return None


def _resolve_agy_tokens(
    trace_path: Path, turns: list[Turn] | None = None
) -> dict[str, int] | None:
    if tokens := _get_tokens_from_metrics(trace_path):
        return tokens
    extracted = _extract_tokens_from_trace(trace_path)
    estimated = _estimate_tokens_from_turns(turns)
    if extracted:
        in_t = extracted["input_tokens"] or (estimated["input_tokens"] if estimated else 100)
        out_t = extracted["output_tokens"] or (estimated["output_tokens"] if estimated else 10)
        return {
            "input_tokens": in_t,
            "output_tokens": out_t,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    if estimated:
        return estimated
    return None


class AgyParser:
    """Parse an `agy` (Antigravity CLI) `transcript.jsonl` into `ParsedTranscript` / `usage/v1`.

    JSONL present → structured turns + tool calls (token metrics from session metrics if measured);
    else → trace-log fallback (`TraceParser`).
    """

    def __init__(self) -> None:
        self._trace = TraceParser()

    def parse(self, jsonl_path: Path | None, trace_path: Path) -> ParsedTranscript:
        lines = self._load_lines(jsonl_path)
        if lines is None:
            if jsonl_path:
                logger.debug("agy transcript.jsonl unusable at %s — trace fallback", jsonl_path)
            parsed = self._trace.parse(None, trace_path)
            agg_tokens = _resolve_agy_tokens(trace_path, parsed.turns)
            if agg_tokens:
                flags = [f for f in parsed.flags if f != FLAG_NO_TOKEN_TELEMETRY]
                return ParsedTranscript(
                    turns=parsed.turns,
                    model_stats=parsed.model_stats,
                    agg_usage=agg_tokens,
                    flags=flags,
                )
            return parsed

        turns = self._parse_lines(lines)
        # HATS-1397: agy rotates its brain segment on a checkpoint and offers no
        # link between the pieces, so the resolved transcript can be a tail
        # fragment of the session. Whichever source carries more of it wins; the
        # trace-only flag is deliberately NOT inherited, because a structured
        # transcript did exist and the record must stay measured.
        traced = self._trace.parse(None, trace_path).turns
        if len(traced) > len(turns):
            logger.debug("agy transcript.jsonl covers %d turns, trace %d — using the trace",
                         len(turns), len(traced))
            turns = traced

        agg_tokens = _resolve_agy_tokens(trace_path, turns)
        if agg_tokens:
            agg_usage = agg_tokens
            flags: list[str] = []
        else:
            agg_usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
            flags = [FLAG_NO_TOKEN_TELEMETRY]

        return ParsedTranscript(
            turns=turns,
            model_stats={},
            agg_usage=agg_usage,
            flags=flags,
        )

    def parse_usage(self, jsonl_path: Path | None, trace_path: Path) -> dict[str, Any]:
        lines = self._load_lines(jsonl_path)
        if lines is None:
            report = self._trace.parse_usage(None, trace_path)
            agg_tokens = _resolve_agy_tokens(trace_path, getattr(report, "turns", None))
            if agg_tokens:
                agg = report["aggregates"]
                agg["input_tokens"] = agg_tokens["input_tokens"]
                agg["output_tokens"] = agg_tokens["output_tokens"]
                agg["cache_read_input_tokens"] = agg_tokens["cache_read_input_tokens"]
                agg["cache_creation_input_tokens"] = agg_tokens["cache_creation_input_tokens"]
                report["flags"] = [f for f in report.get("flags", []) if f != FLAG_NO_TOKEN_TELEMETRY]
            return report

        report = (
            empty_usage_report(Path(jsonl_path).name)
            if jsonl_path
            else empty_usage_report("transcript.jsonl")
        )
        turns = self._parse_lines(lines)
        agg_tokens = _resolve_agy_tokens(trace_path, turns)
        if agg_tokens:
            agg = report["aggregates"]
            agg["input_tokens"] = agg_tokens["input_tokens"]
            agg["output_tokens"] = agg_tokens["output_tokens"]
            agg["cache_read_input_tokens"] = agg_tokens["cache_read_input_tokens"]
            agg["cache_creation_input_tokens"] = agg_tokens["cache_creation_input_tokens"]
            report["flags"] = [f for f in report.get("flags", []) if f != FLAG_NO_TOKEN_TELEMETRY]
        else:
            report["flags"].append(FLAG_NO_TOKEN_TELEMETRY)

        turns = self._parse_lines(lines)
        agg = report["aggregates"]
        timeline = report["timeline"]

        for turn in turns:
            for tool_str in turn.tools:
                agg["tool_calls"] += 1
                tool_name = tool_str.split(":")[0].strip() if ":" in tool_str else tool_str
                timeline.append({"ts": turn.timestamp, "kind": "tool", "name": tool_name})

        return report

    def _load_lines(self, jsonl_path: Path | None) -> list[dict[str, Any]] | None:
        if not jsonl_path:
            return None
        p = Path(jsonl_path)
        if not p.exists():
            return None
        try:
            records = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
            return records
        except (OSError, json.JSONDecodeError):
            return None

    def _parse_lines(self, records: list[dict[str, Any]]) -> list[Turn]:
        turns: list[Turn] = []
        current: Turn | None = None

        for rec in records:
            if not isinstance(rec, dict):
                continue

            source = rec.get("source")
            msg_type = rec.get("type")
            ts = (rec.get("created_at") or "")[:19]

            if msg_type == "USER_INPUT" or source == "USER_EXPLICIT":
                raw_text = rec.get("content", "")
                text = _clean_user_text(raw_text)
                if text:
                    current = Turn(timestamp=ts, user_input=text)
                    turns.append(current)

            elif msg_type == "PLANNER_RESPONSE":
                if current is None:
                    current = Turn(timestamp=ts)
                    turns.append(current)

                thinking = rec.get("thinking", "")
                if thinking:
                    secs = max(1, len(thinking) // 200)
                    current.thinking_secs = max(current.thinking_secs, secs)

                tool_calls = rec.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            name = tc.get("name", "unknown")
                            args = tc.get("args", {})
                            summary = _summarize_tool_args(name, args)
                            current.tools.append(f"{name}: {summary}")

                content = rec.get("content", "")
                if isinstance(content, str) and content.strip():
                    if current.response:
                        current.response += "\n" + content.strip()
                    else:
                        current.response = content.strip()

        return turns
