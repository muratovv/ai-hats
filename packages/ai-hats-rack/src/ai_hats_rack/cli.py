"""``rack`` — minimal JSON-first CLI over the bare kernel (HATS-1020).

Four verbs only (create/show/transition/log); doc and context verbs arrive
with K2/K5. Root resolution is deliberately explicit (``--tasks-dir`` /
``RACK_TASKS_DIR``) — the walk-up project resolver is K2's (HATS-197/839).
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import click

from .dispatch import OperationAborted
from .fsm import InvalidTransitionError, UnknownStateError
from .kernel import (
    ForceRequiresReasonError,
    Kernel,
    KernelResult,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
)
from .models import TaskCard

# Same env contract as the tracker (string value is the shared contract).
ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_TASKS_DIR = "RACK_TASKS_DIR"


def _actor() -> str:
    """Actor identity for the dispatch context: session > human fallback."""
    session = os.environ.get(ENV_SESSION_ID, "")
    if session:
        return f"session:{session}"
    try:
        return f"human:{getpass.getuser()}"
    except OSError:
        return "human:unknown"


def _kernel(tasks_dir: Path) -> Kernel:
    return Kernel(tasks_dir)


def _emit_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(as_json: bool, code: str, message: str, **details: Any) -> None:
    if as_json:
        _emit_json({"error": {"code": code, "message": message, **details}})
    else:
        click.echo(f"error: {message}", err=True)
    sys.exit(1)


def _result_payload(result: KernelResult) -> dict[str, Any]:
    return {
        "task": result.task.to_dict(),
        "transitions": [t.to_dict() for t in result.transitions],
        "journal": [r.to_dict() for r in result.journal],
    }


def _handle_kernel_error(exc: Exception, as_json: bool) -> None:
    """Typed, actionable failures (never a bare traceback for known classes)."""
    if isinstance(exc, InvalidTransitionError):
        # Self-documenting FSM refusal (PROP-061): name the legal edges.
        _fail(
            as_json,
            "invalid_transition",
            str(exc),
            task_id=exc.task_id,
            from_state=exc.from_state,
            to_state=exc.to_state,
            legal_edges=list(exc.allowed),
        )
    if isinstance(exc, UnknownStateError):
        _fail(as_json, "unknown_state", str(exc), known_states=list(exc.known))
    if isinstance(exc, OperationAborted):
        _fail(as_json, "aborted", str(exc), subscriber=exc.subscriber, reason=exc.reason)
    if isinstance(exc, UnknownTaskError):
        _fail(as_json, "unknown_task", str(exc), task_id=exc.task_id)
    if isinstance(exc, TaskExistsError):
        _fail(as_json, "task_exists", str(exc), task_id=exc.task_id)
    if isinstance(exc, (ForceRequiresReasonError, ValueError)):
        _fail(as_json, "invalid_request", str(exc))
    if isinstance(exc, LockTimeoutError):
        _fail(as_json, "lock_timeout", str(exc))
    raise exc


_TASKS_DIR_OPT = click.option(
    "--tasks-dir",
    envvar=ENV_TASKS_DIR,
    default="tasks",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory holding <ID>/task.yaml card dirs.",
)
_JSON_OPT = click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")


@click.group()
def main() -> None:
    """rack — minimal backlog kernel CLI (ai-hats-rack)."""


@main.command()
@click.argument("title")
@click.option("--id", "task_id", default=None, help="Explicit id (default: allocate next).")
@click.option("--description", default="")
@click.option("--priority", default="medium", show_default=True)
@click.option("--role", default="")
@click.option("--reviewer", default="user", show_default=True)
@click.option("--parent", "parent_task", default="", help="Parent task id (epicifies the parent).")
@click.option("--depends", "depends_on", multiple=True)
@click.option("--tag", "tags", multiple=True)
@_TASKS_DIR_OPT
@_JSON_OPT
def create(
    title: str,
    task_id: str | None,
    description: str,
    priority: str,
    role: str,
    reviewer: str,
    parent_task: str,
    depends_on: tuple[str, ...],
    tags: tuple[str, ...],
    tasks_dir: Path,
    as_json: bool,
) -> None:
    """Create a task card (initial state comes from fsm.yaml)."""
    try:
        result = _kernel(tasks_dir).create(
            actor=_actor(),
            caller_cwd=Path.cwd(),
            task_id=task_id,
            title=title,
            description=description,
            priority=priority,
            role=role,
            reviewer=reviewer,
            parent_task=parent_task,
            depends_on=list(depends_on),
            tags=list(tags),
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json(_result_payload(result))
    else:
        click.echo(f"Created: {result.task.id} [{result.task.state}] {result.task.title}")


@main.command()
@click.argument("task_id")
@_TASKS_DIR_OPT
@_JSON_OPT
def show(task_id: str, tasks_dir: Path, as_json: bool) -> None:
    """Show a task card."""
    task = _kernel(tasks_dir).get(task_id)
    if task is None:
        _fail(as_json, "unknown_task", f"Task '{task_id}' not found", task_id=task_id)
        return
    if as_json:
        _emit_json({"task": task.to_dict()})
    else:
        _echo_card(task)


def _echo_card(task: TaskCard) -> None:
    for key in ("id", "title", "state", "priority", "reviewer", "parent_task"):
        value = getattr(task, key)
        if value:
            click.echo(f"  {key}: {value}")
    if task.work_log:
        click.echo("  work_log:")
        for entry in task.work_log[-5:]:
            click.echo(f"    {entry.timestamp} {entry.message}")


@main.command()
@click.argument("task_id")
@click.argument("to_state")
@click.option("--force", is_flag=True, help="Relax the FSM arrow only; requires --reason.")
@click.option("--reason", default="", help="Why (required with --force; journaled).")
@click.option("--resolution", default=None)
@click.option("--final-state", "final_state", default=None)
@_TASKS_DIR_OPT
@_JSON_OPT
def transition(
    task_id: str,
    to_state: str,
    force: bool,
    reason: str,
    resolution: str | None,
    final_state: str | None,
    tasks_dir: Path,
    as_json: bool,
) -> None:
    """Move a task along an FSM edge (refusals print the legal edges)."""
    try:
        result = _kernel(tasks_dir).transition(
            task_id,
            to_state,
            actor=_actor(),
            caller_cwd=Path.cwd(),
            force=force,
            reason=reason,
            resolution=resolution,
            final_state=final_state,
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json(_result_payload(result))
    else:
        for t in result.transitions:
            click.echo(f"Transitioned: {t.task_id} {t.from_state} → {t.to_state}")


@main.command()
@click.argument("task_id")
@click.argument("message")
@_TASKS_DIR_OPT
@_JSON_OPT
def log(task_id: str, message: str, tasks_dir: Path, as_json: bool) -> None:
    """Append a work_log entry to a task card."""
    try:
        task = _kernel(tasks_dir).log_work(task_id, message, actor=_actor())
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json({"task": task.to_dict()})
    else:
        click.echo(f"Logged: {task.id} — {message}")


if __name__ == "__main__":
    main()
