"""`ai-hats retro` / `retro-validate` / `retro-migrate` — retrospective commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


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
@click.option("--backfill", is_flag=True,
              help="Batch-generate retros for all sessions without one (HATS-160)")
@click.option("--dry-run", is_flag=True,
              help="With --backfill: list candidates without running the builder")
@click.option("--since", default=None,
              help="With --backfill: only sessions on or after YYYY-MM-DD")
@click.option("--min-turns", "min_turns", default=1, type=int,
              help="With --backfill: skip sessions below this turn count (default 1)")
@click.option("--only", default=None,
              help="With --backfill: comma-separated list of session ids to process")
@click.option("--force", is_flag=True,
              help="With --backfill: regenerate retros even if a file already exists")
@click.option("--parallel", default=1, type=click.IntRange(min=1),
              help="With --backfill: process N candidates concurrently (default 1 = sequential)")
@click.option("--interactive", is_flag=True,
              help="After generating, hand off to a live `claude` session preloaded with the retro file")
def retro(
    session_id: str | None,
    use_last: bool,
    mode: str,
    timeout: int,
    backfill: bool,
    dry_run: bool,
    since: str | None,
    min_turns: int,
    only: str | None,
    force: bool,
    parallel: int,
    interactive: bool,
):
    """Generate a structured session retrospective (HATS-051 schema)."""
    project_dir = _project_dir()

    if interactive and backfill:
        console.print("[red]--interactive is mutually exclusive with --backfill[/]")
        sys.exit(2)

    if backfill:
        if session_id or use_last:
            console.print("[red]--backfill is mutually exclusive with SESSION_ID / --last[/]")
            sys.exit(2)
        _run_backfill_cli(
            project_dir,
            mode=mode, timeout=timeout, dry_run=dry_run,
            since=since, min_turns=min_turns,
            only=[s.strip() for s in only.split(",")] if only else None,
            force=force, parallel=parallel,
        )
        return

    # Single-session path (unchanged).
    from ..observe import SessionManager
    from ..retro.builder import BuilderMode, SessionRetroBuilder
    from ..retro.llm_caller import SubprocessLLMCaller

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

    if interactive:
        exec_claude_with_retro(path, kind="session")


def _run_backfill_cli(
    project_dir: Path,
    *,
    mode: str,
    timeout: int,
    dry_run: bool,
    since: str | None,
    min_turns: int,
    only: list[str] | None,
    force: bool,
    parallel: int = 1,
) -> None:
    from ..retro.backfill import run_backfill
    from ..retro.builder import BuilderMode

    builder_mode = BuilderMode(mode)
    summary = run_backfill(
        project_dir,
        mode=builder_mode,
        since=since, min_turns=min_turns, only=only, force=force,
        dry_run=dry_run, timeout=timeout, parallel=parallel,
        printer=console.print,
    )

    if summary.total_candidates == 0:
        console.print(
            "[yellow]No candidates[/] — all sessions either have retros "
            f"or were filtered out ({len(summary.pre_filter_skipped)} skipped)."
        )
        return

    if summary.interrupted:
        console.print("\n[yellow]Interrupted[/] — partial summary below.")

    console.print(
        f"\n[bold]Total[/] {summary.total_candidates}: "
        f"[green]saved={summary.saved}[/]  "
        f"[red]failed={summary.failed}[/]  "
        f"[dim]dry_run={summary.dry_run}  "
        f"pre_filter_skipped={len(summary.pre_filter_skipped)}[/]  "
        f"time={summary.total_duration_s:.1f}s"
    )
    if summary.failed:
        sys.exit(1)
    if summary.interrupted:
        sys.exit(130)


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
