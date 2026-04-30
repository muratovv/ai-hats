"""`ai-hats judge` / `ai-hats judge-aggregate` — judge runs and aggregation."""

from __future__ import annotations

import sys

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


@click.command()
@click.option("--bundle", "bundle_id", default=None, help="Bundle id to judge")
@click.option("--sessions", default=None, help="Comma-separated session ids (auto-bundle)")
@click.option(
    "--last", "last_n", default=None, type=int, help="Judge last N sessions (auto-bundle)"
)
@click.option("--focus", default=None, help="Focus lens for the judge")
@click.option("--interactive", is_flag=True,
              help="After judging, hand off to a live `claude` session preloaded with the judge retro")
def judge(
    bundle_id: str | None,
    sessions: str | None,
    last_n: int | None,
    focus: str | None,
    interactive: bool,
):
    """Spawn judge sub-agent over a bundle and validate its output."""
    project_dir = _project_dir()

    from ..retro.judge import JudgeRunner, JudgeValidationError

    runner = JudgeRunner(project_dir)
    session_ids = [s.strip() for s in sessions.split(",")] if sessions else None

    # Show what we're about to judge
    _print_judge_context(runner, bundle_id, session_ids, last_n, focus)

    label = bundle_id or (
        f"sessions={','.join(session_ids)}" if session_ids else f"last={last_n}" if last_n else "?"
    )
    try:
        with console.status(
            f"[cyan]Judging {label} (spawning judge sub-agent, may take a few minutes)...[/]",
            spinner="dots",
        ):
            path = runner.judge(
                bundle_id=bundle_id,
                session_ids=session_ids,
                last_n=last_n,
                focus=focus,
            )
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except JudgeValidationError as exc:
        console.print(f"[red]Judge output failed validation[/]:\n{exc}")
        sys.exit(2)
    console.print(f"[green]Judge retro[/]: {path}")

    if interactive:
        exec_claude_with_retro(path, kind="judge")


def _print_judge_context(
    runner, bundle_id: str | None, session_ids: list[str] | None,
    last_n: int | None, focus: str | None,
) -> None:
    """Show bundle/session info before judging starts."""
    try:
        if bundle_id:
            bundle = runner.bundles.get(bundle_id)
            sids = bundle.session_ids
        elif session_ids:
            sids = session_ids
        elif last_n:
            from ..observe import SessionManager
            sessions = SessionManager(runner.project_dir).list_sessions(last_n=last_n)
            sids = [s.session_id for s in sessions]
        else:
            return
    except (FileNotFoundError, ValueError):
        return

    console.print("[bold]Judge run[/]")
    console.print(f"  Sessions ({len(sids)}):")
    for sid in sids:
        console.print(f"    - {sid}")
    if focus:
        console.print(f"  Focus: [cyan]{focus}[/]")
    console.print()


@click.command("judge-aggregate")
@click.option(
    "--strategy",
    type=click.Choice(["freq"]),
    default="freq",
    help="Aggregation strategy (default: freq)",
)
@click.option("--since", default=None, help="Only include retros since YYYY-MM-DD")
@click.option(
    "--min-severity",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default=None,
    help="Exclude findings below this severity",
)
def judge_aggregate(strategy: str, since: str | None, min_severity: str | None):
    """Aggregate judge retros to surface recurring patterns."""
    from datetime import date as date_cls

    from ..retro.aggregator import Aggregator
    from ..retro.common import Severity

    since_date = date_cls.fromisoformat(since) if since else None
    sev = Severity(min_severity) if min_severity else None

    agg = Aggregator(_project_dir())
    try:
        path = agg.aggregate(strategy=strategy, since=since_date, min_severity=sev)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)

    from ..retro.loader import load

    model, body = load(path)
    console.print(f"[green]Aggregation saved[/]: {path}")
    console.print(body)
