"""``rack doctor`` — backlog integrity report (HATS-1335).

Read-only: scans every backlog mounted in the current project and reports
dangling links, transitive link cycles, mirror drift, duplicate list entries,
missing required fields, and unreadable cards. Exit 0 clean, exit 1 with
findings; repair stays a human ``transition --link/--unlink`` decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, handle_rack_error, resolved_root
from ..doctor import diagnose_workspace
from ..workspace import Workspace
from . import Verb


@click.command("doctor")
@TASKS_DIR_OPT
@JSON_OPT
def doctor_cmd(tasks_dir: Path | None, as_json: bool) -> None:
    """Backlog integrity report over every mounted backlog (read-only)."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        workspace = Workspace.discover([root])
        findings = diagnose_workspace(workspace)
        scanned = {
            (i.definition.cli_alias or i.name): sum(1 for _ in i.catalog.glob("*/task.yaml"))
            for i in workspace.instances
        }
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return
    if as_json:
        payload = {
            "clean": not findings,
            "scanned": scanned,
            "findings": [f.to_dict() for f in findings],
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        cards = sum(scanned.values())
        backlogs = ", ".join(f"{name}: {n}" for name, n in scanned.items())
        if not findings:
            click.echo(f"clean — {cards} cards ({backlogs}), no findings")
        else:
            for f in findings:
                where = f"{f.backlog}/{f.task_id}" if f.backlog else f.task_id
                click.echo(f"{f.check}  {where}  {f.detail}")
            click.echo(f"{len(findings)} finding(s) across {cards} cards ({backlogs})")
    if findings:
        sys.exit(1)


def verb() -> Verb:
    return Verb("doctor", lambda defn: doctor_cmd)
