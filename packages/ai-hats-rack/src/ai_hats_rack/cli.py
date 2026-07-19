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

import importlib.metadata
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import click

from .cli_common import JSON_OPT as _JSON_OPT
from .cli_common import TASKS_DIR_OPT as _TASKS_DIR_OPT
from .cli_common import actor as _actor
from .cli_common import emit_json as _emit_json
from .cli_common import handle_rack_error as _handle_rack_error
from .cli_common import resolved_root as _resolved_root
from .cli_context import context_cmd, ls_cmd
from .definition import resolve_definition
from .dispatch import bind_subscribers, validate_requires_states
from .extensions import standalone_extensions
from .journal import JsonlJournalSink
from .kernel import Kernel, KernelResult
from .ops import parse_ops
from .resolver import RackRoot

#: Entry-point group the integrator advertises its wired-kernel factory under.
#: Discovery (not a static import) keeps the import-hygiene pin intact.
KERNEL_FACTORY_GROUP = "ai_hats_rack.kernel_factory"


@runtime_checkable
class KernelProvider(Protocol):
    """Integrator-contributed wiring, discovered by entry point (HATS-1038 C1)."""

    def build_kernel(self, root: RackRoot, caller_cwd: Path) -> Kernel: ...
    def after_create(self, root: RackRoot, result: KernelResult) -> None: ...
    def handle_error(self, exc: Exception, as_json: bool, task_id: str = ...) -> bool: ...


@lru_cache(maxsize=1)
def _provider() -> KernelProvider | None:
    """First registered wiring factory, or None → bare standalone kernel."""
    for ep in importlib.metadata.entry_points(group=KERNEL_FACTORY_GROUP):
        return ep.load()()
    return None


def _bare_kernel(root: RackRoot) -> Kernel:
    # Standalone mutation surface = kernel + scaffold + plan-gate (epic §2.3):
    # the composite transition still enforces the gate; no ownership/worktree.
    # One backlog definition builds the kernel AND its subscribers (HATS-1042).
    defn = resolve_definition(root.tasks_dir, prefix_alias=root.prefix, project_dir=root.project_dir)
    subscribers = standalone_extensions(root.tasks_dir, topology=defn.topology)
    validate_requires_states(subscribers, defn.topology, source=str(root.tasks_dir))
    kernel = Kernel(
        root.tasks_dir,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        edge_names=defn.edge_names,
        subscribers=subscribers,
        journal_sink=JsonlJournalSink(root.tasks_dir),
    )
    bind_subscribers(subscribers, kernel)
    return kernel


def _build_kernel(
    tasks_dir: Path | None, caller_cwd: Path, provider: KernelProvider | None
) -> tuple[Kernel, RackRoot]:
    """Resolve the root, then build the wired kernel (integrator present) or the
    bare standalone kernel. Every CLI-built kernel journals to audit.jsonl (K7)."""
    root = _resolved_root(tasks_dir, caller_cwd)
    kernel = provider.build_kernel(root, caller_cwd) if provider is not None else _bare_kernel(root)
    return kernel, root


def _echo_deltas(result: KernelResult) -> None:
    """Print work_log deltas subscribers produced this op — the worktree path
    (in-lock) and epic auto-transitions (post-lock, journal-only) — inline, not
    only via a follow-up read (HATS-1038 C1)."""
    seen: set[str] = set()
    for record in result.journal:
        for outcome in record.outcomes:
            if outcome.delta is None:
                continue
            for line in outcome.delta.work_log:
                if line not in seen:
                    seen.add(line)
                    click.echo(f"  {line}")


def _result_payload(result: KernelResult) -> dict[str, Any]:
    return {
        "task": result.task.to_dict(),
        "transitions": [t.to_dict() for t in result.transitions],
        "journal": [r.to_dict() for r in result.journal],
        "ops": [dict(op) for op in result.ops],
    }


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
    """Create a task card (initial state comes from backlog.yaml)."""
    caller_cwd = Path.cwd()
    provider = _provider()
    try:
        kernel, root = _build_kernel(tasks_dir, caller_cwd, provider)
        result = kernel.create(
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
        if provider is not None and provider.handle_error(exc, as_json):
            sys.exit(1)
        _handle_rack_error(exc, as_json)
        return
    if provider is not None:
        provider.after_create(root, result)
    if as_json:
        _emit_json(_result_payload(result))
    else:
        click.echo(f"Created: {result.task.id} [{result.task.state}] {result.task.title}")
        _echo_deltas(result)


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
    provider = _provider()
    try:
        ops = parse_ops(op_tokens)
        kernel, _root = _build_kernel(tasks_dir, caller_cwd, provider)
        result = kernel.transition_ops(
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
        if provider is not None and provider.handle_error(exc, as_json, task_id):
            sys.exit(1)
        _handle_rack_error(exc, as_json)
        return
    if as_json:
        _emit_json(_result_payload(result))
    else:
        _echo_ops(result)
        _echo_deltas(result)


# The whole read surface (show/tree/audit/doc-ls ancestry) lives in these two
# verbs; every mutation is a `transition` op (HATS-1029/1030/1031).
main.add_command(context_cmd)
main.add_command(ls_cmd)


if __name__ == "__main__":
    main()
