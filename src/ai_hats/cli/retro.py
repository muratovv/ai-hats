"""`ai-hats retro` / `retro-validate` / `retro-migrate` — retrospective commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._helpers import _project_dir, console


@click.command()
@click.argument("session_id", required=False)
@click.option("--last", "use_last", is_flag=True, help="Use the most recent session")
@click.option(
    "--mode",
    type=click.Choice(["programmatic", "llm"]),
    default="programmatic",
    help="Builder mode: programmatic (fast, no LLM) or llm (narrative summary)",
)
@click.option(
    "--timeout",
    default=600,
    type=int,
    help="LLM call timeout in seconds (llm/hybrid only, default 600)",
)
def retro(session_id: str | None, use_last: bool, mode: str, timeout: int):
    """Generate a structured session retrospective (HATS-051 schema)."""
    from ..observe import SessionManager
    from ..retro.builder import BuilderMode, SessionRetroBuilder
    from ..retro.llm_caller import SubprocessLLMCaller

    project_dir = _project_dir()
    if use_last or not session_id:
        sessions = SessionManager(project_dir).list_sessions(last_n=1)
        if not sessions:
            console.print("[red]No sessions found[/]")
            sys.exit(1)
        session_id = sessions[0].session_id

    builder_mode = BuilderMode(mode)
    use_llm = builder_mode == BuilderMode.LLM
    llm_caller = SubprocessLLMCaller(project_dir, timeout=timeout) if use_llm else None
    builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)

    def _do_build() -> Path:
        return builder.build_and_save(session_id, mode=builder_mode)

    try:
        if use_llm:
            with console.status(
                f"[cyan]Generating retrospective for {session_id} "
                f"(mode={mode}, timeout={timeout}s, calling LLM)...[/]",
                spinner="dots",
            ):
                path = _do_build()
        else:
            path = _do_build()
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        console.print(f"[red]LLM call failed[/]: {exc}")
        console.print("[dim]Tip: try --timeout 600 or fall back to --mode programmatic[/]")
        sys.exit(1)
    console.print(f"[green]Session retro[/]: {path}")


@click.command("retro-validate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def retro_validate(paths: tuple[Path, ...]) -> None:
    """Validate one or more retro files (session-retro / bundle / judge-retro)."""
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
