"""`ai-hats session` retro commands — integrator-side session review.

The browse commands (list / show / audit) live in ``ai_hats_observe.cli.session``
(core-only, standalone). This module re-attaches the retro subcommands
(``retro`` / ``retro-validate``) — downstream consumers of the integrator
``retro/`` subsystem that cannot move into the core-only package — onto the same
``session`` group. ``cli/__init__.py`` overrides the observe ``_seam`` resolvers
with the integrator's AI_HATS_DIR/yaml-aware versions and mounts the group.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ai_hats_observe.cli.session import session

from ..paths import runs_dir
from ._helpers import _project_dir, console, exec_claude_with_retro

__all__ = ["session"]


# ---- session retro ----


@session.command("retro")
@click.argument("session_id", required=False)
@click.option("--last", "use_last", is_flag=True, help="Use the most recent session")
@click.option(
    "--max-retries", type=int, default=1, show_default=True,
    help="LLM retries on validation failure",
)
@click.option("--interactive", is_flag=True,
              help="After generating, hand off to a live `claude` session preloaded with the retro file")
def session_retro(
    session_id: str | None,
    use_last: bool,
    max_retries: int,
    interactive: bool,
):
    """Generate a structured session review (hats-session-review/v1, single LLM call)."""
    from ai_hats_observe import SessionManager

    from ..retro.session_review_runner import SessionReviewError, SessionReviewRunner

    project_dir = _project_dir()

    if use_last or not session_id:
        sessions = SessionManager(
            project_dir, runs_dir=runs_dir(project_dir)
        ).list_sessions(last_n=1)
        if not sessions:
            console.print("[red]No sessions found[/]")
            sys.exit(1)
        session_id = sessions[0].session_id

    runner = SessionReviewRunner(project_dir)

    try:
        with console.status(
            f"[cyan]Generating session review for {session_id} (single LLM call)...[/]",
            spinner="dots",
        ):
            path = runner.run(session_id, max_retries=max_retries)
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except SessionReviewError as exc:
        console.print(f"[red]session-reviewer failed[/]: {exc}")
        sys.exit(1)
    console.print(f"[green]Session review[/]: {path}")

    if interactive:
        exec_claude_with_retro(path, kind="session")


# ---- session retro-validate ----


@session.command("retro-validate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def session_retro_validate(paths: tuple[Path, ...]) -> None:
    """Validate one or more retro files (session-retro / reflect-session)."""
    import yaml
    from pydantic import ValidationError

    from ..retro.loader import load

    failures = 0
    for path in paths:
        try:
            model, _ = load(path)
            family = type(model).__name__
            console.print(f"[green]OK[/] {path} [dim]({family})[/]")
        except (ValueError, yaml.YAMLError, OSError, ValidationError) as exc:
            failures += 1
            console.print(f"[red]FAIL[/] {path}")
            console.print(f"  [dim]{type(exc).__name__}: {exc}[/]")

    if failures:
        console.print(f"\n[red]{failures}/{len(paths)} files failed validation[/]")
        sys.exit(1)
