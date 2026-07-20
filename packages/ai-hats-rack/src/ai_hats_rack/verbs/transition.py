"""``rack transition`` — the single mutating verb + its op-echo renderers
(HATS-1036 R4). Dual-channel: Click-native lifecycle flags + an UNPROCESSED
op-token stream parsed by :func:`ops.parse_ops` (ignore_unknown_options).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import click

from ..cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, handle_rack_error
from ..cli_kernel import _echo_deltas, _provider, _result_payload, _workspace
from ..kernel import KernelResult
from ..ops import parse_ops
from . import Verb

# ----- op-echo on typed rails (HATS-1033) ------------------------------------

# One renderer per op kind, keyed by the result dict's "op" tag (same shape as
# ops._EXECUTORS). test_error_surface.py pins that the renderer keys cover
# ops.OP_KINDS, so a new op kind fails CI rather than echoing nothing.
_OpRenderer = Callable[["KernelResult", "dict[str, Any]"], None]


def _op_state(result: KernelResult, op: dict[str, Any]) -> None:
    click.echo(f"Transitioned: {result.task.id} {op['from']} → {op['to']}")


def _op_attach(result: KernelResult, op: dict[str, Any]) -> None:
    note = " (overwrote)" if op.get("overwrote") else ""
    click.echo(f"Attached: {op['name']} → {op['path']}{note}")


def _op_freeze(result: KernelResult, op: dict[str, Any]) -> None:
    click.echo(f"Frozen: {op['name']} ({op['digest']})")


def _op_rm(result: KernelResult, op: dict[str, Any]) -> None:
    where = f" (recoverable: {op['trashed_to']})" if op["trashed_to"] else " (no file on disk)"
    click.echo(f"Removed: {op['name']}{where}")
    if op.get("revert"):
        click.echo(f"  revert: {op['revert']}")


def _op_log(result: KernelResult, op: dict[str, Any]) -> None:
    click.echo(f"Logged: {op['message']}")


def _op_fields(result: KernelResult, op: dict[str, Any]) -> None:
    click.echo(f"Fields: {result.task.id} {', '.join(op['names'])}")


def _op_link(result: KernelResult, op: dict[str, Any]) -> None:
    verb = "Linked" if op["changed"] else "Already linked"
    click.echo(f"{verb}: {result.task.id} {op['kind']} {op['target']}")


def _op_unlink(result: KernelResult, op: dict[str, Any]) -> None:
    if op["changed"]:
        click.echo(f"Unlinked: {result.task.id} {', '.join(op['kinds'])} {op['target']}")
        if op.get("revert"):
            click.echo(f"  revert: {op['revert']}")
    else:
        click.echo(f"Not linked: {result.task.id} — {op['target']} (no-op)")


_OP_RENDERERS: dict[str, _OpRenderer] = {
    "state": _op_state,
    "fields": _op_fields,
    "attach": _op_attach,
    "freeze": _op_freeze,
    "rm": _op_rm,
    "log": _op_log,
    "link": _op_link,
    "unlink": _op_unlink,
}


def _echo_ops(result: KernelResult) -> None:
    """Human line per op, in execution order; revert-info on destructive ops."""
    for op in result.ops:
        renderer = _OP_RENDERERS.get(op["op"])
        if renderer is None:  # pragma: no cover — pinned exhaustive over OP_KINDS
            raise RuntimeError(f"no op renderer for kind {op['op']!r}")
        renderer(result, op)


@click.command("transition", context_settings={"ignore_unknown_options": True})
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
@TASKS_DIR_OPT
@JSON_OPT
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
    --log <msg>, --link <kind>:<id>, --unlink [<kind>:]<id>,
    --set <field>=<value>, --append <field>=<json>. Effects of earlier ops are
    visible to later ops; any op aborting rolls the whole sequence back. Old form
    `transition <ID> <state>` is sugar for `--state <state>`, where <state> may
    be a named edge (e.g. `reopen`) resolved against the card's current state.
    """
    caller_cwd = Path.cwd()
    provider = _provider()
    try:
        # Route by the id's prefix first so --set int coercion reads the routed
        # backlog's field types (HATS-1036); tasks-only repos resolve to the same
        # tasks kernel — zero behavior change.
        workspace, _root = _workspace(tasks_dir, caller_cwd, provider)
        field_types = {f.name: f.type for f in workspace.instance_for(task_id).definition.fields}
        ops = parse_ops(op_tokens, field_types=field_types)
        kernel = workspace.kernel_for(task_id)
        result = kernel.transition_ops(
            task_id,
            ops,
            actor=actor(),
            caller_cwd=caller_cwd,
            force=force,
            reason=reason,
            resolution=resolution,
            final_state=final_state,
            ack_frozen=ack_frozen,
        )
        # Post-lock: mirror any changed stored-inverse link onto the target
        # backlog (HATS-1044). A tasks-only backlog declares none — a no-op.
        workspace.mirror_after(task_id, result, actor=actor(), caller_cwd=caller_cwd)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        if provider is not None and provider.handle_error(exc, as_json, task_id):
            sys.exit(1)
        handle_rack_error(exc, as_json)
        return
    if as_json:
        emit_json(_result_payload(result))
    else:
        _echo_ops(result)
        _echo_deltas(result)


def verb() -> Verb:
    return Verb("transition", lambda defn: transition)
