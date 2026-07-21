"""`rack root` — manage the cross-project roots registry (HATS-1081)."""

from __future__ import annotations

from pathlib import Path

import click

from .cli_common import JSON_OPT, emit_json, handle_rack_error
from .roots_registry import add_registered_root, load_registered_roots, remove_registered_root


@click.group("root")
def root_group() -> None:
    """Cross-project roots registry (``~/.ai-hats/roots.yaml``) — the source for
    ``rack ls --projects all``."""


@root_group.command("add")
@click.argument("path")
@JSON_OPT
def root_add(path: str, as_json: bool) -> None:
    """Register a project root (validated: has ``.agent/`` or ``ai-hats.yaml``)."""
    try:
        added = add_registered_root(Path(path))
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return
    if as_json:
        emit_json({"added": str(added), "root_id": added.name})
    else:
        click.echo(f"registered {added.name} → {added}")


@root_group.command("ls")
@JSON_OPT
def root_ls(as_json: bool) -> None:
    """List registered roots (``root_id`` = folder name, path, reachable?)."""
    rows = [
        {"root_id": r.name, "path": str(r), "reachable": r.is_dir()}
        for r in load_registered_roots()
    ]
    if as_json:
        emit_json({"roots": rows})
        return
    if not rows:
        click.echo("no roots registered — add with `rack root add <path>`")
        return
    for row in rows:
        mark = "" if row["reachable"] else "  (unreachable)"
        click.echo(f"  {row['root_id']:20} {row['path']}{mark}")


@root_group.command("rm")
@click.argument("path")
@JSON_OPT
def root_rm(path: str, as_json: bool) -> None:
    """Unregister a root by path."""
    removed = remove_registered_root(Path(path))
    if as_json:
        emit_json({"removed": removed})
    else:
        click.echo("removed" if removed else "not registered")
