"""``rack create`` — the schema-driven create verb (HATS-1036 R4/R7).

Step 1 moves the verb here byte-for-byte; step 2 replaces the hand-written
options with ones generated from the backlog ``fields[]`` (``on_user_schema``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, handle_rack_error
from ..cli_kernel import _build_kernel, _echo_deltas, _provider, _result_payload
from . import Verb


@click.command("create")
@click.argument("title")
@click.option("--id", "task_id", default=None, help="Explicit id (default: allocate next).")
@click.option("--description", default=None)
@click.option("--priority", default=None)
@click.option("--role", default=None)
@click.option("--reviewer", default=None)
@click.option("--parent", "parent_task", default="", help="Parent task id (epicifies the parent).")
@click.option("--depends", "depends_on", multiple=True)
@click.option("--tag", "tags", multiple=True)
@TASKS_DIR_OPT
@JSON_OPT
def create(
    title: str,
    task_id: str | None,
    description: str | None,
    priority: str | None,
    role: str | None,
    reviewer: str | None,
    parent_task: str,
    depends_on: tuple[str, ...],
    tags: tuple[str, ...],
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Create a task card (initial state + field defaults come from backlog.yaml)."""
    caller_cwd = Path.cwd()
    provider = _provider()
    try:
        kernel, root = _build_kernel(tasks_dir, caller_cwd, provider)
        result = kernel.create(
            actor=actor(),
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
        if provider is not None and provider.handle_error(exc, as_json):
            sys.exit(1)
        handle_rack_error(exc, as_json)
        return
    if provider is not None:
        provider.after_create(root, result)
    if as_json:
        emit_json(_result_payload(result))
    else:
        click.echo(f"Created: {result.task.id} [{result.task.state}] {result.task.title}")
        _echo_deltas(result)


def verb() -> Verb:
    return Verb("create", lambda defn: create)
