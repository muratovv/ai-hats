"""``rack audit <ID>`` — query surface over the K7 audit journal (HATS-1025).

Human-readable feed + ``--json`` (JSON-first, stable v1 record schema);
nothing is ever truncated (PROP-004). An empty journal on a task that already
moved states is flagged (PROP-005/076 zero-events: the sink was missing or
its writes failed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .cli_common import JSON_OPT, TASKS_DIR_OPT, fail, resolved_root
from .fsm import load_topology
from .journal import CorruptLine, read_journal
from .models import TaskCard
from .resolver import NoProjectRootError


@click.command()
@click.argument("task_id")
@click.option(
    "--event", "event_key", default=None, help="Exact event key (e.g. edge:plan--execute)."
)
@click.option("--since", default=None, help="ISO-8601 UTC lower bound on ts (inclusive).")
@click.option("--actor", default=None, help="Exact actor (e.g. session:<id>).")
@TASKS_DIR_OPT
@JSON_OPT
def audit(
    task_id: str,
    event_key: str | None,
    since: str | None,
    actor: str | None,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Show the dispatch audit journal of a task."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
    except NoProjectRootError as exc:
        fail(as_json, "no_project_root", str(exc))
        return
    card_path = root.tasks_dir / task_id / "task.yaml"
    if not card_path.exists():
        fail(as_json, "unknown_task", f"Task '{task_id}' not found", task_id=task_id)
        return

    records, corrupt = read_journal(root.tasks_dir, task_id)
    warnings = _warnings(card_path, records, corrupt)
    filtered = [r for r in records if _matches(r, event_key, since, actor)]

    if as_json:
        payload: dict[str, Any] = {"task_id": task_id, "records": filtered, "warnings": warnings}
        if corrupt:
            payload["corrupt"] = [c.to_dict() for c in corrupt]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not filtered:
        click.echo("(no journal records)")
    for record in filtered:
        _echo_record(record)
    for warning in warnings:
        click.echo(f"warning: {warning}")


def _warnings(
    card_path: Path, records: list[dict[str, Any]], corrupt: list[CorruptLine]
) -> list[str]:
    out: list[str] = []
    state = TaskCard.from_yaml(card_path).state
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
    record: dict[str, Any], event_key: str | None, since: str | None, actor: str | None
) -> bool:
    if event_key is not None and record.get("event") != event_key:
        return False
    if since is not None and record.get("ts", "") < since:
        return False
    if actor is not None and record.get("actor") != actor:
        return False
    return True


def _echo_record(record: dict[str, Any]) -> None:
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
    click.echo(head)
    if record.get("reason"):
        click.echo(f"    reason: {record['reason']}")
    for outcome in record.get("outcomes", []):
        line = f"    {outcome.get('subscriber')} ({outcome.get('phase')}): {outcome.get('outcome')}"
        if outcome.get("reason"):
            line += f" — {outcome['reason']}"
        if outcome.get("delta"):
            line += f" delta={json.dumps(outcome['delta'], ensure_ascii=False)}"
        click.echo(line)


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
