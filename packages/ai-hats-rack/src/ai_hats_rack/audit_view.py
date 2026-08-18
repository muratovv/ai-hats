"""Journal view — the query + format layer over the K7 audit journal, now read
through ``context --attr audit`` (HATS-1029: the ``audit`` verb died, its view
lives). Nothing is truncated (PROP-004); a dark journal on a moved task is
flagged (PROP-005/076).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .fsm import load_topology
from .journal import CorruptLine, read_journal


@dataclass(frozen=True)
class JournalView:
    """Filtered feed + provenance warnings + any raw torn lines."""

    records: list[dict[str, Any]]
    warnings: list[str]
    corrupt: list[CorruptLine]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"records": self.records, "warnings": self.warnings}
        if self.corrupt:
            out["corrupt"] = [c.to_dict() for c in self.corrupt]
        return out


def journal_view(
    tasks_dir: Path,
    task_id: str,
    state: str,
    *,
    event: str | None = None,
    since: str | None = None,
    actor: str | None = None,
) -> JournalView:
    """Read the task journal, warn on a dark trail, then AND-filter the feed."""
    records, corrupt = read_journal(tasks_dir, task_id)
    warnings = _warnings(state, records, corrupt)
    filtered = [r for r in records if _matches(r, event, since, actor)]
    return JournalView(filtered, warnings, corrupt)


def _warnings(state: str, records: list[dict[str, Any]], corrupt: list[CorruptLine]) -> list[str]:
    out: list[str] = []
    if not records and state != load_topology().initial:
        out.append(
            f"zero-events: task is in '{state}' but its audit journal is empty — "
            "transitions ran without a journal sink or every write failed "
            "(PROP-005/076); this history is unauditable."
        )
    for line in corrupt:
        out.append(
            f"corrupt journal line {line.file}:{line.line_no} (torn write?) — "
            "raw text preserved, shown only in --json output"
        )
    return out


#: The retired spelling of an edge event key. Records written before HATS-1719
#: keep it forever, so a READER understands both — while nothing writes it again.
_RETIRED_EDGE_PREFIX = "edge:"


def _canonical_event(key: str) -> str:
    """One spelling to compare by: the retired ``edge:`` form reads as an arrow.

    A rename with history behind it needs a reader that spans it; only the
    writer moved (design.md §1.6). A retired key without the two dashes — the
    named-edge alias ``edge:reclaim`` — has no arrow form and is left alone.
    """
    if not key.startswith(_RETIRED_EDGE_PREFIX):
        return key
    source, sep, target = key[len(_RETIRED_EDGE_PREFIX) :].partition("--")
    return f"{source}->{target}" if sep and source and target else key


def _matches(
    record: dict[str, Any], event: str | None, since: str | None, actor: str | None
) -> bool:
    if event is not None and _canonical_event(str(record.get("event", ""))) != _canonical_event(
        event
    ):
        return False
    if since is not None and record.get("ts", "") < since:
        return False
    if actor is not None and record.get("actor") != actor:
        return False
    return True


_DETAIL_KEY_HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "from": lambda d: f"{d['from']} → {d.get('to', '')}",
    "child": lambda d: f"child {d['child']}",
    "kind": lambda d: f"{d['kind']} {d.get('target', '')}".strip(),
    "field": lambda d: f"{d['field']} {d.get('op', '')}".strip(),
    "name": lambda d: f"{d.get('op', '')} {d['name']}".strip(),
    "message": lambda d: f"{d['message']}",
    "operation": lambda d: f"{d['operation']}",
}


def _format_detail(detail: dict[str, Any]) -> str:
    for key, handler in _DETAIL_KEY_HANDLERS.items():
        if key in detail:
            return f" [{handler(detail)}]"
    return ""


def record_lines(record: dict[str, Any]) -> list[str]:
    """One head line per record + indented reason/outcomes. No truncation."""
    head = (
        f"{record.get('ts', '?')} {record.get('event', '?')}"
        f"{_format_detail(record.get('detail') or {})}"
        f" actor={record.get('actor', '')} result={record.get('result', '')}"
    )
    marks = _marks(record)
    if marks:
        head += "  [" + ", ".join(marks) + "]"
    lines = [head]
    if record.get("reason"):
        lines.append(f"  reason: {record['reason']}")
    for outcome in record.get("outcomes", []):
        line = f"  {outcome.get('subscriber')} ({outcome.get('phase')}): {outcome.get('outcome')}"
        if outcome.get("reason"):
            line += f" — {outcome['reason']}"
        if outcome.get("delta"):
            line += f" delta={json.dumps(outcome['delta'], ensure_ascii=False)}"
        lines.append(line)
    return lines


def _marks(record: dict[str, Any]) -> list[str]:
    identity = record.get("identity") or {}
    marks = []
    if record.get("force"):
        marks.append("forced")
    if identity.get("verdict") == "mismatch":
        marks.append("IDENTITY MISMATCH")
    if identity.get("verdict") == "unverified":
        marks.append("identity unverified")
    if identity.get("holder_mismatch"):
        marks.append(f"HOLDER MISMATCH (holder session:{identity.get('holder', '')})")
    return marks
