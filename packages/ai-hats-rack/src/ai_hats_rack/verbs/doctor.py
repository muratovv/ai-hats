"""``rack doctor`` — integrity report over the backlog AND its declared gates.

Read-only. Cards (HATS-1335): dangling links, transitive link cycles, mirror
drift, duplicate list entries, missing required fields, unreadable cards. Gates
(HATS-1584): one line per point of every carried row, classified against every
mounted topology — the distinction a subscriber cannot make, holding one.
Repair stays a human ``transition --link/--unlink`` decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, handle_rack_error
from ..cli_kernel import _provider, _workspace
from ..doctor import BindingReport, diagnose_bindings, diagnose_workspace
from . import Verb


@click.command("doctor")
@TASKS_DIR_OPT
@JSON_OPT
def doctor_cmd(tasks_dir: Path | None, as_json: bool) -> None:
    """Backlog + binding report over every mounted backlog (read-only).

    Exit 0 clean, 1 with findings, 1 on a typed failure, 2 on a usage error.
    Findings and failure SHARE the code, so a caller tells them apart by shape
    under ``--json``: a run that finished reports {clean, scanned, findings,
    bindings}, one that could not start reports {error} and nothing else.
    Branch on the ``error`` key, never on the exit code (HATS-1584).
    """  # comment-length: allow — this table IS the contract callers branch on
    try:
        workspace, root = _workspace(tasks_dir, Path.cwd(), _provider())
        findings = diagnose_workspace(workspace)
        bindings = diagnose_bindings(workspace, owner=root.backlog_owner)
        findings += list(bindings.findings)
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
            "bindings": {"note": bindings.note, "rows": [r.to_dict() for r in bindings.rows]},
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        cards = sum(scanned.values())
        backlogs = ", ".join(f"{name}: {n}" for name, n in scanned.items())
        for f in findings:
            # A binding finding sits on no card, so the card column is dropped
            # rather than rendered as a bare separator (HATS-1584).
            where = "/".join(part for part in (f.backlog, f.task_id) if part)
            click.echo(f"{f.check}  {where}  {f.detail}" if where else f"{f.check}  {f.detail}")
        _echo_bindings(bindings)
        if findings:
            click.echo(f"{len(findings)} finding(s) across {cards} cards ({backlogs})")
        else:
            click.echo(f"clean — {cards} cards ({backlogs}), no findings")
    if findings:
        sys.exit(1)


def _echo_bindings(report: BindingReport) -> None:
    """The gate roster, printed even when empty: "nothing listed" and "nothing
    could be read" are different facts and only one of them is fine."""
    click.echo(f"bindings — {report.note}" if report.note else f"bindings ({len(report.rows)}):")
    for row in report.rows:
        where = f"apps.rack.{row.backlog}" if row.backlog else "apps.rack"
        click.echo(f"  {row.status:<12} {where}  {row.selector or '-'}  {row.label}")


def verb() -> Verb:
    return Verb("doctor", lambda defn: doctor_cmd)
