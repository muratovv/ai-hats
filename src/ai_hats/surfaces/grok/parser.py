"""Grok session parser — ``updates.jsonl`` plus the sibling ``usage.json``.

On disk, ``inputTokens`` is the full prompt and cache reads sit inside it.
Canonical ``input_tokens`` is the uncached remainder. ``usage.json`` is the
aggregate; summing ``turn_completed`` on top of it would count the turn twice.
Stdout's snake_case projector is a different shape and is not read here.
An unknown ``sessionUpdate`` passes silently. A shape break is a flag, never
an exception.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY
from ai_hats_observe.canonical.signals import Notice, WorthRecording
from ai_hats_observe.canonical.types import now
from ai_hats_observe.parsers.base import ParsedTranscript, Turn
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.usage import empty_usage_report

_SOURCE = "grok/updates"
_AGG_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _empty_agg() -> dict[str, int]:
    return {key: 0 for key in _AGG_KEYS}


def _timestamp(event: dict) -> str:
    params = event.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    millis = meta.get("agentTimestampMs") if isinstance(meta, dict) else None
    if isinstance(millis, (int, float)):
        raw, scale = millis, 1000.0
    else:
        stamp = event.get("timestamp")
        if not isinstance(stamp, (int, float)):
            return ""
        raw, scale = stamp, 1.0
    try:
        return datetime.fromtimestamp(raw / scale, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _update(event: dict) -> dict | None:
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    return update if isinstance(update, dict) else None


def _chunk_text(update: dict) -> str | None:
    content = update.get("content")
    if not isinstance(content, dict) or "text" not in content:
        return None
    if content.get("type") not in (None, "text"):
        return None
    return str(content.get("text") or "")


def _tool_name(update: dict) -> str:
    meta = update.get("_meta")
    if not isinstance(meta, dict):
        return ""
    tool = meta.get("x.ai/tool")
    if isinstance(tool, dict) and tool.get("name"):
        return str(tool["name"])
    if isinstance(tool, str):
        return tool
    return ""


def _amounts(blob: dict) -> dict[str, int] | None:
    """Uncached input plus the three siblings. ``None`` when the numbers lie.

    A negative remainder means ``inputTokens`` is not the full prompt this
    parser was measured against — inventing a zero would hide that.
    """
    try:
        full = int(blob.get("inputTokens") or 0)
        output = int(blob.get("outputTokens") or 0)
        cache_read = int(blob.get("cachedReadTokens") or 0)
        cache_create = int(blob.get("cacheCreationTokens") or 0)
        calls = int(blob.get("modelCalls") or 0)
    except (TypeError, ValueError):
        return None
    uncached = full - cache_read - cache_create
    if uncached < 0:
        return None
    return {
        "input_tokens": uncached,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        "calls": calls,
    }


class _Ledger:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.stats: dict[str, dict] = {}
        self.agg = _empty_agg()
        self.flags: list[str] = []
        self.signals: list[Notice] = []
        self.api_calls = 0
        self.entry_types: dict[str, int] = {}
        self.tool_calls = 0
        self.hook_firings = 0
        self.session_id: str | None = None
        self.measured = False

    def drift(self, detail: str) -> None:
        self.flags.append("schema-drift")
        self.signals.append(
            Notice(
                reason=WorthRecording.UNSUPPORTED_RECORD,
                detail=detail,
                source=_SOURCE,
                ts=now(),
            )
        )


def _apply_usage(ledger: _Ledger, blob: dict, *, model: str, where: str) -> None:
    amounts = _amounts(blob)
    if amounts is None:
        ledger.drift(f"{where}: usage is not a full-prompt ledger")
        return
    ledger.measured = True
    for key in _AGG_KEYS:
        ledger.agg[key] += amounts[key]
    ledger.api_calls += amounts["calls"]
    row = ledger.stats.setdefault(model, {"in": 0, "out": 0, "calls": 0})
    row["calls"] += amounts["calls"]
    row["in"] += amounts["input_tokens"]
    row["out"] += amounts["output_tokens"]


def _apply_model_usage(ledger: _Ledger, session: dict, *, where: str) -> None:
    usage = session.get("modelUsage")
    if isinstance(usage, dict) and usage:
        for model, blob in usage.items():
            if not isinstance(blob, dict):
                ledger.drift(f"{where}: modelUsage[{model!r}] is not an object")
                continue
            _apply_usage(ledger, blob, model=str(model), where=f"{where}:{model}")
        return
    model = str(session.get("primaryModelId") or "unknown")
    _apply_usage(ledger, session, model=model, where=where)


def _load_session(jsonl: Path, ledger: _Ledger) -> dict | None:
    sibling = jsonl.parent / "usage.json"
    if not sibling.is_file():
        return None
    try:
        data = json.loads(sibling.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        ledger.drift(f"{sibling.name}: unparsable ({exc})")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("session"), dict):
        ledger.drift(f"{sibling.name}: no session object")
        return None
    if isinstance(data.get("sessionId"), str):
        ledger.session_id = data["sessionId"]
    return data["session"]


def _open(ledger: _Ledger, event: dict) -> Turn:
    turn = Turn(timestamp=_timestamp(event), user_input="")
    ledger.turns.append(turn)
    return turn


def _current(ledger: _Ledger, event: dict) -> Turn:
    if not ledger.turns:
        return _open(ledger, event)
    return ledger.turns[-1]


def _on_user(ledger: _Ledger, event: dict, update: dict, where: str) -> None:
    text = _chunk_text(update)
    if text is None:
        ledger.drift(f"{where}: user_message_chunk has no text content")
        return
    last = ledger.turns[-1] if ledger.turns else None
    if last is None or last.response or last.tools:
        _open(ledger, event).user_input = text
        return
    last.user_input = (last.user_input or "") + text


def _on_agent(ledger: _Ledger, event: dict, update: dict, where: str, *, thought: bool) -> None:
    text = _chunk_text(update)
    if text is None:
        kind = "agent_thought_chunk" if thought else "agent_message_chunk"
        ledger.drift(f"{where}: {kind} has no text content")
        return
    turn = _current(ledger, event)
    if thought:
        if text:
            turn.thinking.append(text)
        return
    turn.response += text


def _on_tool(ledger: _Ledger, event: dict, update: dict, where: str, seen: set[str]) -> None:
    name = _tool_name(update)
    if not name:
        ledger.drift(f"{where}: tool_call names no tool")
        return
    call_id = str(update.get("toolCallId") or "")
    if call_id and call_id in seen:
        return
    if call_id:
        seen.add(call_id)
    ledger.tool_calls += 1
    _current(ledger, event).tools.append(name)


def _walk(ledger: _Ledger, path: Path, *, have_session: bool, seen: set[str]) -> None:
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        where = f"{path.name}:{lineno}"
        try:
            event = json.loads(line)
        except ValueError:
            ledger.drift(f"{where}: unparsable line")
            continue
        if not isinstance(event, dict):
            ledger.drift(f"{where}: record is not an object")
            continue
        update = _update(event)
        if update is None:
            continue
        kind = update.get("sessionUpdate")
        if not isinstance(kind, str) or not kind:
            ledger.drift(f"{where}: session update has no type")
            continue
        ledger.entry_types[kind] = ledger.entry_types.get(kind, 0) + 1
        if kind == "user_message_chunk":
            _on_user(ledger, event, update, where)
        elif kind == "agent_message_chunk":
            _on_agent(ledger, event, update, where, thought=False)
        elif kind == "agent_thought_chunk":
            _on_agent(ledger, event, update, where, thought=True)
        elif kind == "tool_call":
            _on_tool(ledger, event, update, where, seen)
        elif kind == "hook_execution":
            ledger.hook_firings += 1
        elif kind == "turn_completed" and not have_session:
            usage = update.get("usage")
            if not isinstance(usage, dict):
                ledger.drift(f"{where}: turn_completed has no usage object")
                continue
            _apply_model_usage(ledger, usage, where=where)


def _read(paths: list[Path]) -> _Ledger:
    ledger = _Ledger()
    session = None
    for path in paths:
        found = _load_session(path, ledger)
        if found is not None:
            session = found
            break
    if session is not None:
        _apply_model_usage(ledger, session, where="usage.json")
    seen: set[str] = set()
    for path in paths:
        _walk(ledger, path, have_session=session is not None, seen=seen)
    if not ledger.measured:
        ledger.flags.append(FLAG_NO_TOKEN_TELEMETRY)
    ledger.flags = list(dict.fromkeys(ledger.flags))
    return ledger


class GrokParser:
    """``TranscriptParser`` over grok's ``updates.jsonl``."""

    def __init__(self, *, trace: TraceParser | None = None) -> None:
        self._trace = trace or TraceParser()

    def parse(self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path) -> ParsedTranscript:
        paths = self._normalize(jsonl_path)
        if not paths:
            return self._trace.parse(None, trace_path)
        ledger = _read(paths)
        return ParsedTranscript(
            turns=ledger.turns,
            model_stats=ledger.stats,
            agg_usage=ledger.agg,
            flags=ledger.flags,
            signals=ledger.signals,
        )

    def parse_usage(self, jsonl_path: Path | Iterable[Path] | None, trace_path: Path) -> dict:
        paths = self._normalize(jsonl_path)
        if not paths:
            return self._trace.parse_usage(None, trace_path)
        ledger = _read(paths)
        report = empty_usage_report(paths[0].name)
        report["session_id"] = ledger.session_id
        report["api_calls"] = ledger.api_calls
        report["entry_types_seen"] = ledger.entry_types
        report["flags"] = list(ledger.flags)
        for key in _AGG_KEYS:
            report["usage_totals"][key] = ledger.agg[key]
        report["aggregates"]["tool_calls"] = ledger.tool_calls
        report["aggregates"]["hook_firings"] = ledger.hook_firings
        return report

    @staticmethod
    def _normalize(jsonl_path: Path | Iterable[Path] | None) -> list[Path]:
        if jsonl_path is None:
            return []
        if isinstance(jsonl_path, (Path, str)):
            path = Path(jsonl_path)
            return [path] if path.exists() else []
        return [Path(path) for path in jsonl_path if Path(path).exists()]


__all__ = ["GrokParser"]
