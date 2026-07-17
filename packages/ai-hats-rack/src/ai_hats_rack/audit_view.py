"""Journal view — the query + format layer over the K7 audit journal, now read
through ``context --attr audit`` (HATS-1029: the ``audit`` verb died, its view
lives). Nothing is truncated (PROP-004); a dark journal on a moved task is
flagged (PROP-005/076).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _matches(
    record: dict[str, Any], event: str | None, since: str | None, actor: str | None
) -> bool:
    if event is not None and record.get("event") != event:
        return False
    if since is not None and record.get("ts", "") < since:
        return False
    if actor is not None and record.get("actor") != actor:
        return False
    return True


def record_lines(record: dict[str, Any]) -> list[str]:
    """One head line per record + indented reason/outcomes. No truncation."""
    head = f"{record.get('ts', '?')} {record.get('event', '?')}"
    detail = record.get("detail") or {}
    if "from" in detail:
        head += f" [{detail['from']} → {detail['to']}]"
    elif "child" in detail:
        head += f" [child {detail['child']}]"
    elif "operation" in detail:
        head += f" [{detail['operation']}]"
    head += f" actor={record.get('actor', '')} result={record.get('result', '')}"
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
