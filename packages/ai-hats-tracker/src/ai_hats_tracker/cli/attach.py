"""`ai-hats task attach` — manage per-task file attachments (HATS-402).

Subcommands:
  add, list, show, remove, verify

Storage: blobs live in ``<tasks_dir>/<ID>/attachments/<name>``; the manifest
lives in ``task.yaml::attachments[]`` as ``{name, digest, added, note}``
records. Digest is a 12-char SHA-256 prefix (48 bits) — see HATS-402 plan.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..attachments import (
    DivergenceKind,
    ReconcileAction,
    attachments_dir,
    is_binary,
    is_git_tracked,
)
from . import _seam


@click.group()
def attach():
    """Manage per-task file attachments."""


@attach.command("add")
@click.argument("task_id")
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--name", default="", help="Override attachment name (default: basename)")
@click.option("--note", default="", help="Free-text note for this attachment")
def attach_add(task_id: str, path: Path, name: str, note: str):
    """Attach a file to TASK_ID.

    Idempotent: re-adding the same content under the same name is a no-op.
    Same name with different content is a hard error — use 'attach remove'
    first if you intend to replace.
    """
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    attach_name = name or path.name
    try:
        result = mgr.attach_add(task_id, path.resolve(), attach_name, note=note)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)

    if result.action is ReconcileAction.ERROR_COLLISION:
        _seam._CONSOLE.print(
            f"[red]Collision[/]: name {attach_name!r} already attached with "
            f"different content (existing digest: {result.existing_digest}, "
            f"new: {result.new_digest})."
        )
        _seam._CONSOLE.print(
            f"  Run [bold]ai-hats task attach remove {task_id} {attach_name}[/] "
            "first if you intend to replace."
        )
        sys.exit(1)

    if result.action is ReconcileAction.NOOP:
        _seam._CONSOLE.print(
            f"[yellow]Noop[/]: '{attach_name}' already attached, "
            f"digest matches ({result.attachment.digest if result.attachment else ''})."
        )
        return

    assert result.attachment is not None
    verb = "Attached" if result.action is ReconcileAction.ADDED else "Registered"
    _seam._CONSOLE.print(
        f"[green]{verb}[/]: '{attach_name}' (digest {result.attachment.digest}) to {task_id}"
    )


@attach.command("list")
@click.argument("task_id")
def attach_list(task_id: str):
    """List attachments on TASK_ID."""
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    task = mgr.get_task(task_id)
    if task is None:
        _seam._CONSOLE.print(f"[red]Error[/]: Task '{task_id}' not found")
        sys.exit(1)

    if not task.attachments:
        _seam._CONSOLE.print("(no attachments)")
        return

    from rich.table import Table

    tbl = Table(show_header=True, header_style="bold")
    tbl.add_column("name")
    tbl.add_column("digest")
    tbl.add_column("added")
    tbl.add_column("note")
    for a in task.attachments:
        tbl.add_row(a.name, a.digest, a.added, a.note)
    _seam._CONSOLE.print(tbl)


@attach.command("show")
@click.argument("task_id")
@click.argument("name")
def attach_show(task_id: str, name: str):
    """Print an attachment's content to stdout (binaries: print path + warn)."""
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    task = mgr.get_task(task_id)
    if task is None:
        _seam._CONSOLE.print(f"[red]Error[/]: Task '{task_id}' not found")
        sys.exit(1)

    entry = next((a for a in task.attachments if a.name == name), None)
    if entry is None:
        _seam._CONSOLE.print(f"[red]Error[/]: '{name}' not attached to {task_id}")
        sys.exit(1)

    blob_path = attachments_dir(mgr.tasks_dir / task_id) / name
    if not blob_path.is_file():
        _seam._CONSOLE.print(
            f"[red]Error[/]: manifest entry exists but blob missing at {blob_path}"
        )
        sys.exit(1)

    if is_binary(blob_path):
        click.echo(str(blob_path))
        click.echo(
            f"warning: binary content not printed (digest {entry.digest})",
            err=True,
        )
        return
    click.echo(blob_path.read_text(), nl=False)


@attach.command("remove")
@click.argument("task_id")
@click.argument("name")
@click.option(
    "--yes",
    "yes",
    is_flag=True,
    default=False,
    help="Confirm deletion of an untracked blob (no git recovery)",
)
def attach_remove(task_id: str, name: str, yes: bool):
    """Detach NAME from TASK_ID (remove manifest entry + blob).

    Tracked blobs delete without confirmation (restorable via git). Untracked
    blobs require --yes — deletion is permanent.
    """
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    task = mgr.get_task(task_id)
    if task is None:
        _seam._CONSOLE.print(f"[red]Error[/]: Task '{task_id}' not found")
        sys.exit(1)

    entry = next((a for a in task.attachments if a.name == name), None)
    if entry is None:
        _seam._CONSOLE.print(f"[yellow]Noop[/]: '{name}' not attached to {task_id}")
        return

    blob_path = attachments_dir(mgr.tasks_dir / task_id) / name
    tracked = blob_path.is_file() and is_git_tracked(blob_path)

    if not tracked and not yes:
        _seam._CONSOLE.print(
            f"[red]Refusing[/]: '{name}' is untracked — deletion is permanent "
            "(no git recovery). Pass --yes to confirm."
        )
        sys.exit(2)

    try:
        _, removed, _ = mgr.attach_remove(task_id, name)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)

    if removed is None:
        _seam._CONSOLE.print(f"[yellow]Noop[/]: '{name}' was not in manifest")
        return

    if tracked:
        _seam._CONSOLE.print(
            f"[green]Removed[/]: '{name}' (git-tracked — restorable via [bold]git restore[/])"
        )
    else:
        _seam._CONSOLE.print(f"[green]Removed[/]: '{name}' (permanent)")


@attach.command("verify")
@click.argument("task_id")
def attach_verify(task_id: str):
    """Internal: report manifest/disk divergences for TASK_ID.

    Used by the pre-commit hook. Exit 0 silent on clean state; exit 1 with
    a machine-friendly listing on divergence.
    """
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        divs = mgr.attach_verify(task_id)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    if not divs:
        return
    for d in divs:
        kind_label = {
            DivergenceKind.BLOB_WITHOUT_ENTRY: "+",
            DivergenceKind.ENTRY_WITHOUT_BLOB: "-",
            DivergenceKind.DIGEST_DRIFT: "~",
        }[d.kind]
        click.echo(f"{kind_label} {d.name}")
    sys.exit(1)
