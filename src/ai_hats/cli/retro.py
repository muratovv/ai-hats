"""`ai-hats retro` / `retro-validate` — retrospective commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


@click.command()
@click.argument("session_id", required=False)
@click.option("--last", "use_last", is_flag=True, help="Use the most recent session")
@click.option(
    "--timeout",
    default=600,
    type=int,
    help="LLM call timeout in seconds (default 600)",
)
@click.option("--interactive", is_flag=True,
              help="After generating, hand off to a live `claude` session preloaded with the retro file")
def retro(
    session_id: str | None,
    use_last: bool,
    timeout: int,
    interactive: bool,
):
    """Generate a structured session retrospective (HATS-051 schema, LLM mode)."""
    from ..observe import SessionManager
    from ..retro.builder import SessionRetroBuilder
    from ..retro.llm_caller import SubprocessLLMCaller

    project_dir = _project_dir()

    if use_last or not session_id:
        sessions = SessionManager(project_dir).list_sessions(last_n=1)
        if not sessions:
            console.print("[red]No sessions found[/]")
            sys.exit(1)
        session_id = sessions[0].session_id

    llm_caller = SubprocessLLMCaller(project_dir, timeout=timeout)
    builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)

    try:
        with console.status(
            f"[cyan]Generating retrospective for {session_id} "
            f"(timeout={timeout}s, calling LLM)...[/]",
            spinner="dots",
        ):
            path = builder.build_and_save(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        console.print(f"[red]LLM call failed[/]: {exc}")
        console.print("[dim]Tip: try --timeout 600[/]")
        sys.exit(1)
    console.print(f"[green]Session retro[/]: {path}")

    if interactive:
        exec_claude_with_retro(path, kind="session")


@click.command("retro-validate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def retro_validate(paths: tuple[Path, ...]) -> None:
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
