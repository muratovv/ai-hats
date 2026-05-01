"""`ai-hats reflect` — full session-review cycle in one command (HATS-201).

Pipeline:
  1. retro --backfill   — generate per-session retros for sessions missing them
  2. bundle create      — bundle the still-unreviewed sessions
  3. judge              — run judge sub-agent over that bundle
  4. judge-aggregate    — cluster findings across all judge retros
  5. --interactive      — optional handoff to live `claude` with the aggregate

Designed as the destination of the session-end reminder: one command, one
systemic outcome, instead of nudging the user through four separate commands.
Each stage is skipped silently when there is nothing to do.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date as _date, datetime
from pathlib import Path

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


@click.command()
@click.option("--since", default=None,
              help="Only include sessions / judge retros on or after YYYY-MM-DD")
@click.option("--min-turns", "min_turns", default=1, type=int,
              help="Skip sessions below this turn count (default 1)")
@click.option("--parallel", default=2, type=click.IntRange(min=1),
              help="Backfill: process N candidates concurrently (default 2)")
@click.option("--mode", type=click.Choice(["programmatic", "llm"]),
              default="programmatic", help="Backfill builder mode (default programmatic)")
@click.option("--focus", default=None, help="Focus lens passed to judge")
@click.option(
    "--min-severity",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default=None, help="Aggregate: exclude findings below this severity",
)
@click.option("--interactive", is_flag=True,
              help="After aggregation, hand off to live `claude` preloaded with the report")
@click.option("--dry-run", is_flag=True,
              help="Show what would run; do not write retros / bundles / aggregations")
def reflect(
    since: str | None,
    min_turns: int,
    parallel: int,
    mode: str,
    focus: str | None,
    min_severity: str | None,
    interactive: bool,
    dry_run: bool,
):
    """Full session-review cycle: backfill → bundle → judge → aggregate.

    Stages with nothing to do are skipped silently. If the whole pipeline has
    nothing to add, exits 0 with "Nothing to do".
    """
    project_dir = _project_dir()
    started = time.monotonic()

    header = (
        f"[bold]Reflect[/]  since={since or '(all)'}  mode={mode}  parallel={parallel}"
        + ("  [yellow]DRY-RUN[/]" if dry_run else "")
    )
    console.print(header)

    backfill_summary = _stage_backfill(
        project_dir,
        since=since, min_turns=min_turns, mode=mode,
        parallel=parallel, dry_run=dry_run,
    )

    bundle_id, bundle_size = _stage_bundle(
        project_dir,
        since=since, min_turns=min_turns, dry_run=dry_run,
    )

    judge_path: Path | None = None
    judge_failed = False
    if bundle_id is None:
        console.print("\n[bold cyan]▸ Stage 3: judge bundle[/]")
        console.print("  [dim]skipped (no new bundle)[/]")
    elif dry_run:
        console.print("\n[bold cyan]▸ Stage 3: judge bundle[/]")
        cmd = f"ai-hats judge --bundle {bundle_id}"
        if focus:
            cmd += f" --focus {focus!r}"
        console.print(f"  [dim]would run: {cmd}[/]")
    else:
        try:
            judge_path = _stage_judge(project_dir, bundle_id=bundle_id, focus=focus)
        except Exception as exc:  # noqa: BLE001 — surface any judge failure to user
            console.print(f"  [red]judge step failed[/]: {exc}")
            judge_failed = True

    agg_path: Path | None = None
    if dry_run:
        console.print("\n[bold cyan]▸ Stage 4: aggregate judge retros[/]")
        cmd = "ai-hats judge-aggregate"
        if since:
            cmd += f" --since {since}"
        if min_severity:
            cmd += f" --min-severity {min_severity}"
        console.print(f"  [dim]would run: {cmd}[/]")
    else:
        agg_path = _stage_aggregate(project_dir, since=since, min_severity=min_severity)

    duration = time.monotonic() - started
    _print_summary(
        backfill=backfill_summary,
        bundle_id=bundle_id, bundle_size=bundle_size,
        judge_path=judge_path, judge_failed=judge_failed,
        agg_path=agg_path, duration=duration,
    )

    if judge_failed:
        sys.exit(1)

    nothing_done = (
        not dry_run
        and backfill_summary.saved == 0
        and bundle_id is None
        and judge_path is None
        and agg_path is None
    )
    if nothing_done:
        console.print("[yellow]Nothing to do.[/]")
        return

    if interactive and not dry_run and agg_path is not None:
        exec_claude_with_retro(agg_path, kind="aggregate")


# --- stages --------------------------------------------------------------


def _stage_backfill(project_dir: Path, *, since, min_turns, mode, parallel, dry_run):
    from ..retro.backfill import run_backfill
    from ..retro.builder import BuilderMode

    console.print("\n[bold cyan]▸ Stage 1: backfill missing session retros[/]")
    summary = run_backfill(
        project_dir,
        mode=BuilderMode(mode),
        since=since, min_turns=min_turns, force=False,
        dry_run=dry_run, parallel=parallel,
        printer=console.print,
    )
    if summary.total_candidates == 0:
        console.print("  [dim]nothing to backfill[/]")
    else:
        console.print(
            f"  [green]saved={summary.saved}[/] "
            f"[red]failed={summary.failed}[/] "
            f"[dim]dry_run={summary.dry_run} "
            f"time={summary.total_duration_s:.1f}s[/]"
        )
    return summary


def _stage_bundle(project_dir: Path, *, since, min_turns, dry_run):
    """Bundle still-unreviewed sessions.

    Returns (bundle_id, size). `bundle_id` is None when there is nothing to
    bundle. In dry-run mode with candidates, returns ("(dry-run)", N) so
    downstream stages can show what *would* happen.
    """
    from ..observe import SessionManager
    from ..retro.bundles import BundleManager

    console.print("\n[bold cyan]▸ Stage 2: bundle unreviewed sessions[/]")
    bm = BundleManager(project_dir)
    reviewed = bm.reviewed_session_ids()
    sessions = SessionManager(project_dir).list_sessions(productive_only=True)

    since_date = _date.fromisoformat(since) if since else None
    candidates: list[str] = []
    for s in sessions:
        if s.session_id in reviewed:
            continue
        if since_date is not None:
            try:
                ts = datetime.strptime(s.session_id[:8], "%Y%m%d").date()
            except ValueError:
                continue
            if ts < since_date:
                continue
        if min_turns > 0 and s.metrics_path.exists():
            try:
                m = json.loads(s.metrics_path.read_text())
            except (OSError, ValueError):
                continue
            turns = int(m.get("turns", 0) or 0)
            tool_calls = int(m.get("tool_calls", 0) or 0)
            if turns < min_turns and tool_calls == 0:
                continue
        candidates.append(s.session_id)

    if not candidates:
        console.print("  [dim]no unreviewed sessions[/]")
        return None, 0

    console.print(f"  [green]{len(candidates)} unreviewed session(s)[/]")
    if dry_run:
        for sid in candidates[:10]:
            console.print(f"    - {sid}")
        if len(candidates) > 10:
            console.print(f"    ... +{len(candidates) - 10} more")
        return "(dry-run)", len(candidates)

    bundle = bm.create(candidates, notes=f"reflect since={since or '(all)'}")
    console.print(
        f"  [green]bundle:[/] {bundle.bundle_id} "
        f"({len(bundle.session_ids)} session(s))"
    )
    return bundle.bundle_id, len(bundle.session_ids)


def _stage_judge(project_dir: Path, *, bundle_id: str, focus: str | None) -> Path:
    from ..retro.judge import JudgeRunner

    console.print("\n[bold cyan]▸ Stage 3: judge bundle[/]")
    runner = JudgeRunner(project_dir)
    label = bundle_id + (f"  focus={focus}" if focus else "")
    with console.status(
        f"[cyan]Judging {label} (spawning judge sub-agent, may take a few minutes)...[/]",
        spinner="dots",
    ):
        path = runner.judge(bundle_id=bundle_id, focus=focus)
    console.print(f"  [green]judge retro:[/] {path}")
    return path


def _stage_aggregate(project_dir: Path, *, since, min_severity) -> Path | None:
    from ..retro.aggregator import Aggregator
    from ..retro.common import Severity

    console.print("\n[bold cyan]▸ Stage 4: aggregate judge retros[/]")
    since_date = _date.fromisoformat(since) if since else None
    sev = Severity(min_severity) if min_severity else None
    agg = Aggregator(project_dir)
    try:
        path = agg.aggregate(strategy="freq", since=since_date, min_severity=sev)
    except ValueError as exc:
        # "No judge retros" / "No findings match" — terminal "all-skipped" path.
        console.print(f"  [yellow]{exc}[/]")
        return None
    console.print(f"  [green]aggregate:[/] {path}")
    return path


def _print_summary(*, backfill, bundle_id, bundle_size,
                   judge_path, judge_failed, agg_path, duration) -> None:
    console.print("\n[bold]── Reflect summary ──[/]")
    console.print(
        f"  Backfill:  saved={backfill.saved}  "
        f"failed={backfill.failed}  dry_run={backfill.dry_run}"
    )
    if bundle_id:
        console.print(f"  Bundle:    {bundle_id} ({bundle_size} session(s))")
    else:
        console.print("  Bundle:    [dim]skipped[/]")
    if judge_path:
        console.print(f"  Judge:     {judge_path}")
    elif judge_failed:
        console.print("  Judge:     [red]FAILED[/]")
    else:
        console.print("  Judge:     [dim]skipped[/]")
    if agg_path:
        console.print(f"  Aggregate: {agg_path}")
    else:
        console.print("  Aggregate: [dim]skipped[/]")
    console.print(f"  [dim]Total time: {duration:.1f}s[/]")
