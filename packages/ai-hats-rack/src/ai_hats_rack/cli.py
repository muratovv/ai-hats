"""``rack`` — minimal JSON-first CLI over the bare kernel (HATS-1020).

Four verbs (HATS-1031 API-D surface): ``create``; ``transition`` — the single
mutating verb, an ordered composite of ops under one lock (HATS-1030, with
``--log`` as the work-log op since ``log`` died); ``context`` — the single
read package (``show`` folded in, ``audit`` as ``--attr``); ``ls`` — backlog
scan / graph walk (``tree`` folded into ``ls --deep``). Root resolution
defaults to the validated walk-up resolver (HATS-197/839, K2); ``--tasks-dir``
/ ``RACK_TASKS_DIR`` stay as the explicit override.
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
from .cli_context import context_cmd, ls_cmd
from .dispatch import OperationAborted
from .docstore import (
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
from .ops import AttachSourceError, OpParseError, parse_ops
from .registry import DerivedLinkKindError, UnknownLinkKindError
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
@click.option(
    "--ack-frozen",
    is_flag=True,
    help="Confirm touching a FROZEN document: --rm (still trashed) or --freeze of drifted content.",
)
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


# The whole read surface (show/tree/audit/doc-ls ancestry) lives in these two
# verbs; every mutation is a `transition` op (HATS-1029/1030/1031).
main.add_command(context_cmd)
main.add_command(ls_cmd)


if __name__ == "__main__":
    main()
