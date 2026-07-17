"""``rack`` — minimal JSON-first CLI over the bare kernel (HATS-1020).

Verbs: create/show/transition/log (K1), the ``doc`` group (K2), ``audit`` (K7),
tree/link/unlink/context/ls (K5). Root
resolution defaults to the validated walk-up resolver (HATS-197/839, K2);
``--tasks-dir`` / ``RACK_TASKS_DIR`` stay as the explicit override.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from .cli_audit import audit
from .cli_common import JSON_OPT as _JSON_OPT
from .cli_common import TASKS_DIR_OPT as _TASKS_DIR_OPT
from .cli_common import actor as _actor
from .cli_common import emit_json as _emit_json
from .cli_common import fail as _fail
from .cli_common import resolved_root as _resolved_root
from .cli_context import context_cmd, ls_cmd, tree_cmd
from .cli_doc import doc, echo_documents
from .dispatch import OperationAborted
from .docstore import (
    DocStore,
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
)
from .extensions import standalone_extensions
from .fsm import InvalidTransitionError, UnknownStateError
from .journal import JsonlJournalSink
from .kernel import (
    ForceRequiresReasonError,
    Kernel,
    KernelResult,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
)
from .linked import SelfLinkError
from .models import TaskCard
from .ops import AttachSourceError, OpParseError, parse_ops
from .registry import (
    DerivedLinkKindError,
    UnknownLinkKindError,
    load_registry_for,
    resolve_links,
)
from .resolver import NoProjectRootError


def _kernel(tasks_dir: Path | None, caller_cwd: Path) -> Kernel:
    root = _resolved_root(tasks_dir, caller_cwd)
    # The mutation surface is standalone = kernel + scaffold + plan-gate (epic
    # §2.3), so the composite transition actually enforces the gate; every
    # CLI-built kernel journals dispatches to tasks/<ID>/audit.jsonl (K7).
    return Kernel(
        root.tasks_dir,
        prefix=root.prefix,
        subscribers=standalone_extensions(root.tasks_dir),
        journal_sink=JsonlJournalSink(root.tasks_dir),
    )


def _result_payload(result: KernelResult) -> dict[str, Any]:
    return {
        "task": result.task.to_dict(),
        "transitions": [t.to_dict() for t in result.transitions],
        "journal": [r.to_dict() for r in result.journal],
        "ops": [dict(op) for op in result.ops],
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
    # ----- composite-op refusals (--attach/--freeze/--rm/--link/--unlink) -----
    if isinstance(exc, OpParseError):
        _fail(as_json, "invalid_ops", str(exc))
    if isinstance(exc, AttachSourceError):
        _fail(as_json, "attach_source", str(exc), src=exc.src)
    if isinstance(exc, DocumentNameError):
        _fail(as_json, "invalid_document_name", str(exc), name=exc.name)
    if isinstance(exc, UnknownDocumentError):
        _fail(as_json, "unknown_document", str(exc), task_id=exc.task_id, name=exc.name)
    if isinstance(exc, FrozenDocumentError):
        _fail(as_json, "frozen_document", str(exc), task_id=exc.task_id, name=exc.name)
    if isinstance(exc, FrozenPinDriftError):
        _fail(
            as_json,
            "frozen_pin_drift",
            str(exc),
            task_id=exc.task_id,
            name=exc.name,
            pinned_digest=exc.pinned,
            current_digest=exc.current,
        )
    if isinstance(exc, SelfLinkError):
        _fail(as_json, "self_link", str(exc), task_id=exc.task_id)
    if isinstance(exc, UnknownLinkKindError):
        _fail(as_json, "unknown_link_kind", str(exc), kind=exc.kind, configured=list(exc.configured))
    if isinstance(exc, DerivedLinkKindError):
        _fail(as_json, "derived_link_kind", str(exc), kind=exc.kind, inverse=exc.inverse)
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


main.add_command(audit)  # `rack audit <ID>` — K7 journal query surface


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
        registry = load_registry_for(root.project_dir)
        kernel = Kernel(root.tasks_dir, registry=registry)
        task = kernel.get(task_id)
        if task is None:
            _fail(as_json, "unknown_task", f"Task '{task_id}' not found", task_id=task_id)
            return
        store = DocStore(root.tasks_dir)
        docs = store.scan(task_id)
        links = _resolve_card_links(kernel, registry, task)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json(
            {"task": task.to_dict(), "links": links, "documents": [d.to_dict() for d in docs]}
        )
    else:
        _echo_card(task, links)
        echo_documents(store.card_dir(task_id), docs)


def _resolve_card_links(kernel: Kernel, registry, task: TaskCard) -> dict[str, list[str]]:
    """The single top-level ``links`` object (HATS-1028): every configured kind
    → its ids, derived children included via the kernel's reverse scan."""
    derived: dict[str, list[str]] = {}
    children_kind = registry.children_kind
    if children_kind is not None:
        derived[children_kind.name] = kernel.children_of(task.id)
    return resolve_links(registry, task, derived=derived)


def _echo_card(task: TaskCard, links: dict[str, list[str]]) -> None:
    for key in ("id", "title", "state", "priority", "reviewer"):
        value = getattr(task, key)
        if value:
            click.echo(f"  {key}: {value}")
    if links:
        click.echo("  links:")
        for kind, ids in links.items():
            click.echo(f"    {kind}: {', '.join(ids)}")
    if task.work_log:
        click.echo("  work_log:")
        for entry in task.work_log[-5:]:
            click.echo(f"    {entry.timestamp} {entry.message}")


def _echo_ops(result: KernelResult) -> None:
    """Human line per op, in execution order; revert-info on destructive ops."""
    for op in result.ops:
        kind = op["op"]
        if kind == "state":
            click.echo(f"Transitioned: {result.task.id} {op['from']} → {op['to']}")
        elif kind == "attach":
            note = " (overwrote)" if op.get("overwrote") else ""
            click.echo(f"Attached: {op['name']} → {op['path']}{note}")
        elif kind == "freeze":
            click.echo(f"Frozen: {op['name']} ({op['digest']})")
        elif kind == "rm":
            where = f" (recoverable: {op['trashed_to']})" if op["trashed_to"] else " (no file on disk)"
            click.echo(f"Removed: {op['name']}{where}")
            if op.get("revert"):
                click.echo(f"  revert: {op['revert']}")
        elif kind == "log":
            click.echo(f"Logged: {op['message']}")
        elif kind == "link":
            verb = "Linked" if op["changed"] else "Already linked"
            click.echo(f"{verb}: {result.task.id} {op['kind']} {op['target']}")
        elif kind == "unlink":
            if op["changed"]:
                click.echo(f"Unlinked: {result.task.id} {', '.join(op['kinds'])} {op['target']}")
                if op.get("revert"):
                    click.echo(f"  revert: {op['revert']}")
            else:
                click.echo(f"Not linked: {result.task.id} — {op['target']} (no-op)")


@main.command(context_settings={"ignore_unknown_options": True})
@click.argument("task_id")
@click.argument("op_tokens", nargs=-1, type=click.UNPROCESSED)
@click.option("--force", is_flag=True, help="Relax the FSM arrow only (state ops); requires --reason.")
@click.option("--reason", default="", help="Why (required with --force; journaled).")
@click.option("--resolution", default=None)
@click.option("--final-state", "final_state", default=None)
@click.option("--ack-frozen", is_flag=True, help="Confirm --rm of a FROZEN document (still trashed).")
@_TASKS_DIR_OPT
@_JSON_OPT
def transition(
    task_id: str,
    op_tokens: tuple[str, ...],
    force: bool,
    reason: str,
    resolution: str | None,
    final_state: str | None,
    ack_frozen: bool,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Ordered composite transition — the single mutating verb.

    Ops run in argv order under ONE task lock with a single persist:
    --state <s>, --attach <src>[:name], --freeze <name>, --rm <name>,
    --log <msg>, --link <kind>:<id>, --unlink [<kind>:]<id>. Effects of earlier
    ops are visible to later ops (a state op's plan-gate sees a just-attached
    plan); any op aborting rolls the whole sequence back. Old form
    `transition <ID> <state>` is sugar for `--state <state>`.
    """
    caller_cwd = Path.cwd()
    try:
        ops = parse_ops(op_tokens)
        result = _kernel(tasks_dir, caller_cwd).transition_ops(
            task_id,
            ops,
            actor=_actor(),
            caller_cwd=caller_cwd,
            force=force,
            reason=reason,
            resolution=resolution,
            final_state=final_state,
            ack_frozen=ack_frozen,
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        _handle_kernel_error(exc, as_json)
        return
    if as_json:
        _emit_json(_result_payload(result))
    else:
        _echo_ops(result)


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
# K5 (HATS-1024): linked tasks + one-call discovery context. link/unlink were
# absorbed into `transition --link/--unlink` (HATS-1030); read verbs stay.
main.add_command(tree_cmd)
main.add_command(context_cmd)
main.add_command(ls_cmd)


if __name__ == "__main__":
    main()
