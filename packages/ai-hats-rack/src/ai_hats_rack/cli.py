"""``rack`` — minimal JSON-first CLI over the bare kernel (HATS-1020).

Verbs: create/show/transition/log (K1) + the ``doc`` group (K2). Root
resolution defaults to the validated walk-up resolver (HATS-197/839, K2);
``--tasks-dir`` / ``RACK_TASKS_DIR`` stay as the explicit override.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from .cli_common import JSON_OPT as _JSON_OPT
from .cli_common import TASKS_DIR_OPT as _TASKS_DIR_OPT
from .cli_common import actor as _actor
from .cli_common import emit_json as _emit_json
from .cli_common import fail as _fail
from .cli_common import resolved_root as _resolved_root
from .cli_doc import doc, echo_documents
from .dispatch import OperationAborted
from .docstore import DocStore
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
from .resolver import NoProjectRootError


def _kernel(tasks_dir: Path | None, caller_cwd: Path) -> Kernel:
    root = _resolved_root(tasks_dir, caller_cwd)
    return Kernel(root.tasks_dir, prefix=root.prefix)


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
    if isinstance(exc, NoProjectRootError):
        _fail(as_json, "no_project_root", str(exc))
    if isinstance(exc, (ForceRequiresReasonError, ValueError)):
        _fail(as_json, "invalid_request", str(exc))
    if isinstance(exc, LockTimeoutError):
        _fail(as_json, "lock_timeout", str(exc))
    raise exc


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
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Create a task card (initial state comes from fsm.yaml)."""
    caller_cwd = Path.cwd()
    try:
        result = _kernel(tasks_dir, caller_cwd).create(
            actor=_actor(),
            caller_cwd=caller_cwd,
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
def show(task_id: str, tasks_dir: Path | None, as_json: bool) -> None:
    """Show a task card + its documents (names and absolute paths, no content)."""
    try:
        root = _resolved_root(tasks_dir, Path.cwd())
        task = Kernel(root.tasks_dir).get(task_id)
        if task is None:
            _fail(as_json, "unknown_task", f"Task '{task_id}' not found", task_id=task_id)
            return
        store = DocStore(root.tasks_dir)
        docs = store.scan(task_id)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json({"task": task.to_dict(), "documents": [d.to_dict() for d in docs]})
    else:
        _echo_card(task)
        echo_documents(store.card_dir(task_id), docs)


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
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Move a task along an FSM edge (refusals print the legal edges)."""
    caller_cwd = Path.cwd()
    try:
        result = _kernel(tasks_dir, caller_cwd).transition(
            task_id,
            to_state,
            actor=_actor(),
            caller_cwd=caller_cwd,
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
def log(task_id: str, message: str, tasks_dir: Path | None, as_json: bool) -> None:
    """Append a work_log entry to a task card."""
    try:
        task = _kernel(tasks_dir, Path.cwd()).log_work(task_id, message, actor=_actor())
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json({"task": task.to_dict()})
    else:
        click.echo(f"Logged: {task.id} — {message}")


main.add_command(doc)


if __name__ == "__main__":
    main()
