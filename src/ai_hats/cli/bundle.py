"""`ai-hats bundle` — manage session bundles for judge analysis."""

from __future__ import annotations

import sys

import click

from ._helpers import _project_dir, console


@click.group()
def bundle():
    """Manage session bundles for judge analysis."""


@bundle.command("create")
@click.option("--sessions", default=None, help="Comma-separated session ids")
@click.option("--last", "last_n", default=None, type=int, help="Use last N sessions")
@click.option("--since", default=None, help="Use sessions since YYYY-MM-DD")
@click.option("--unreviewed", is_flag=True, help="Use all productive sessions not yet in any bundle")
@click.option("--min-turns", default=0, type=int, help="With --unreviewed: minimum turns filter")
@click.option("--notes", default=None, help="Free-form notes")
def bundle_create(
    sessions: str | None,
    last_n: int | None,
    since: str | None,
    unreviewed: bool,
    min_turns: int,
    notes: str | None,
):
    """Create a new bundle artifact (lens-agnostic; pass --focus on `judge`)."""
    import json
    from datetime import date as _date

    from ..retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    try:
        if unreviewed:
            from ..observe import SessionManager
            all_sessions = SessionManager(_project_dir()).list_sessions(productive_only=True)
            reviewed = bm.reviewed_session_ids()
            ids = []
            for s in all_sessions:
                if s.session_id in reviewed:
                    continue
                if min_turns > 0 and s.metrics_path.exists():
                    try:
                        m = json.loads(s.metrics_path.read_text())
                        if m.get("turns", 0) < min_turns:
                            continue
                    except (json.JSONDecodeError, OSError):
                        continue
                ids.append(s.session_id)
            if not ids:
                console.print("[yellow]No unreviewed sessions found[/]")
                sys.exit(0)
            b = bm.create(ids, notes=notes)
        elif sessions:
            ids = [s.strip() for s in sessions.split(",") if s.strip()]
            b = bm.create(ids, notes=notes)
        elif last_n:
            b = bm.create_from_last(last_n, notes=notes)
        elif since:
            b = bm.create_from_since(_date.fromisoformat(since), notes=notes)
        else:
            console.print("[red]Specify one of: --sessions, --last, --since, --unreviewed[/]")
            sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    console.print(f"[green]Bundle[/]: {b.bundle_id}")
    console.print(f"  Sessions: {len(b.session_ids)}")


@bundle.command("list")
def bundle_list():
    """List existing bundles."""
    from ..retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    bundles = bm.list()
    if not bundles:
        console.print("[dim]No bundles[/]")
        return
    for b in bundles:
        notes = f" — {b.notes}" if b.notes else ""
        console.print(f"  {b.bundle_id}  ({len(b.session_ids)} session(s)){notes}")


@bundle.command("show")
@click.argument("bundle_id")
def bundle_show(bundle_id: str):
    """Show contents of one bundle."""
    from ..retro.bundles import BundleManager

    bm = BundleManager(_project_dir())
    try:
        b = bm.get(bundle_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    console.print(f"[bold]{b.bundle_id}[/]")
    console.print(f"  Project: {b.project}")
    console.print(f"  Created: {b.created.isoformat()}")
    if b.notes:
        console.print(f"  Notes: {b.notes}")
    console.print("  Sessions:")
    for sid in b.session_ids:
        console.print(f"    - {sid}")
