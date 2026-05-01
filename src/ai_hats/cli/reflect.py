"""`ai-hats reflect` — full session-review cycle in one command (HATS-201).

Pipeline:
  1. retro --backfill   — generate per-session retros for sessions missing them
  2. bundle create      — bundle the still-unreviewed sessions, optionally
                          chunked into N-sized batches (oldest-first)
  3. judge              — run judge sub-agent over each bundle
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
from dataclasses import dataclass
from datetime import date as _date, datetime
from pathlib import Path

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


@dataclass
class _ChunkResult:
    """Outcome of one (bundle → judge) pass over a chunk of sessions."""
    bundle_id: str
    bundle_size: int
    judge_path: Path | None = None
    error: str | None = None


@click.command()
@click.option("--since", default=None,
              help="Only include sessions / judge retros on or after YYYY-MM-DD")
@click.option("--until", default=None,
              help="Only include sessions strictly before YYYY-MM-DD (exclusive). "
                   "Combined with --since gives the half-open window [since, until), "
                   "so consecutive runs cover adjacent disjoint intervals.")
@click.option("--min-turns", "min_turns", default=1, type=int,
              help="Skip sessions below this turn count (default 1)")
@click.option("--parallel", default=2, type=click.IntRange(min=1),
              help="Backfill: process N candidates concurrently (default 2)")
@click.option("--chunk", "chunk", default=None, type=click.IntRange(min=1),
              help="Bundle/judge in chunks of N sessions (oldest first). "
                   "Default: one bundle for all unreviewed sessions.")
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
    until: str | None,
    min_turns: int,
    parallel: int,
    chunk: int | None,
    mode: str,
    focus: str | None,
    min_severity: str | None,
    interactive: bool,
    dry_run: bool,
):
    """Full session-review cycle: backfill → bundle → judge → aggregate.

    Stages with nothing to do are skipped silently. With ``--chunk N`` the
    bundle/judge stages run once per chunk of N oldest unreviewed sessions;
    a chunk failure does not prevent later chunks from running, and the
    final aggregation includes whatever judge retros succeeded.
    """
    project_dir = _project_dir()
    started = time.monotonic()

    header_bits = [
        "[bold]Reflect[/]",
        f"since={since or '(all)'}",
        f"until={until or '(now)'}",
        f"mode={mode}",
        f"parallel={parallel}",
    ]
    if chunk is not None:
        header_bits.append(f"chunk={chunk}")
    if dry_run:
        header_bits.append("[yellow]DRY-RUN[/]")
    console.print("  ".join(header_bits))

    backfill_summary = _stage_backfill(
        project_dir,
        since=since, until=until, min_turns=min_turns, mode=mode,
        parallel=parallel, dry_run=dry_run,
    )

    chunks = _stage_bundle(
        project_dir,
        since=since, until=until, min_turns=min_turns,
        chunk=chunk, dry_run=dry_run,
    )

    judge_results = _stage_judge_chunks(
        project_dir, chunks=chunks, focus=focus, dry_run=dry_run,
    )
    any_judge_failed = any(r.error is not None for r in judge_results)

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
        chunks=judge_results,
        agg_path=agg_path, duration=duration,
    )

    if any_judge_failed:
        sys.exit(1)

    nothing_done = (
        not dry_run
        and backfill_summary.saved == 0
        and not judge_results
        and agg_path is None
    )
    if nothing_done:
        console.print("[yellow]Nothing to do.[/]")
        return

    if interactive and not dry_run and agg_path is not None:
        exec_claude_with_retro(agg_path, kind="aggregate")


# --- stages --------------------------------------------------------------


def _stage_backfill(project_dir: Path, *, since, until, min_turns, mode,
                    parallel, dry_run):
    from ..retro.backfill import run_backfill
    from ..retro.builder import BuilderMode

    console.print("\n[bold cyan]▸ Stage 1: backfill missing session retros[/]")
    summary = run_backfill(
        project_dir,
        mode=BuilderMode(mode),
        since=since, until=until, min_turns=min_turns, force=False,
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


def _stage_bundle(project_dir: Path, *, since, until, min_turns,
                  chunk: int | None, dry_run: bool) -> list[_ChunkResult]:
    """Build one or more bundles over still-unreviewed sessions.

    With ``chunk`` set, the unreviewed list is sorted oldest-first and split
    into fixed-size batches; each batch becomes its own bundle. Returns a
    list of ``_ChunkResult`` (one per bundle); empty list when there is
    nothing to bundle. In dry-run mode, returns synthetic chunks so stage 3
    can show what *would* happen.
    """
    from ..observe import SessionManager
    from ..retro.bundles import BundleManager

    console.print("\n[bold cyan]▸ Stage 2: bundle unreviewed sessions[/]")
    bm = BundleManager(project_dir)
    reviewed = bm.reviewed_session_ids()
    sessions = SessionManager(project_dir).list_sessions(productive_only=True)

    since_date = _date.fromisoformat(since) if since else None
    until_date = _date.fromisoformat(until) if until else None
    candidates: list[str] = []
    for s in sessions:
        if s.session_id in reviewed:
            continue
        try:
            ts = datetime.strptime(s.session_id[:8], "%Y%m%d").date()
        except ValueError:
            ts = None
        if since_date is not None and (ts is None or ts < since_date):
            continue
        if until_date is not None and (ts is None or ts >= until_date):
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
        return []

    candidates.sort()  # session_id starts with YYYYMMDD-HHMMSS → chronological asc
    batches = _chunked(candidates, chunk) if chunk else [candidates]

    console.print(
        f"  [green]{len(candidates)} unreviewed session(s)[/]"
        + (f" → {len(batches)} chunk(s) of up to {chunk}" if chunk else "")
    )
    if dry_run:
        for i, batch in enumerate(batches, start=1):
            console.print(f"  [dim]chunk {i}/{len(batches)}[/] ({len(batch)} sessions):")
            for sid in batch[:5]:
                console.print(f"    - {sid}")
            if len(batch) > 5:
                console.print(f"    ... +{len(batch) - 5} more")
        return [
            _ChunkResult(bundle_id=f"(dry-run #{i})", bundle_size=len(b))
            for i, b in enumerate(batches, start=1)
        ]

    note_window = f"since={since or '(all)'} until={until or '(now)'}"
    results: list[_ChunkResult] = []
    for i, batch in enumerate(batches, start=1):
        notes = f"reflect chunk {i}/{len(batches)} {note_window}"
        bundle = bm.create(batch, notes=notes)
        console.print(
            f"  [green]chunk {i}/{len(batches)}:[/] {bundle.bundle_id} "
            f"({len(bundle.session_ids)} session(s))"
        )
        results.append(_ChunkResult(
            bundle_id=bundle.bundle_id, bundle_size=len(bundle.session_ids),
        ))
    return results


def _stage_judge_chunks(project_dir: Path, *, chunks: list[_ChunkResult],
                        focus: str | None, dry_run: bool) -> list[_ChunkResult]:
    """Judge each chunk in turn. Failures don't stop later chunks."""
    if not chunks:
        console.print("\n[bold cyan]▸ Stage 3: judge bundle[/]")
        console.print("  [dim]skipped (no new bundle)[/]")
        return []

    console.print("\n[bold cyan]▸ Stage 3: judge bundle(s)[/]")
    if dry_run:
        for i, ch in enumerate(chunks, start=1):
            cmd = f"ai-hats judge --bundle {ch.bundle_id}"
            if focus:
                cmd += f" --focus {focus!r}"
            console.print(f"  [dim]chunk {i}/{len(chunks)}: would run: {cmd}[/]")
        return chunks

    from ..retro.judge import JudgeRunner

    runner = JudgeRunner(project_dir)
    for i, ch in enumerate(chunks, start=1):
        label = ch.bundle_id + (f"  focus={focus}" if focus else "")
        try:
            with console.status(
                f"[cyan]Chunk {i}/{len(chunks)}: judging {label} "
                "(spawning judge sub-agent, may take a few minutes)...[/]",
                spinner="dots",
            ):
                ch.judge_path = runner.judge(bundle_id=ch.bundle_id, focus=focus)
            console.print(
                f"  [green]chunk {i}/{len(chunks)}:[/] {ch.judge_path}"
            )
        except Exception as exc:  # noqa: BLE001 — surface and continue
            ch.error = str(exc) or repr(exc)
            console.print(
                f"  [red]chunk {i}/{len(chunks)} failed[/] ({ch.bundle_id}): {ch.error}"
            )
    return chunks


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


def _print_summary(*, backfill, chunks: list[_ChunkResult], agg_path,
                   duration) -> None:
    console.print("\n[bold]── Reflect summary ──[/]")
    console.print(
        f"  Backfill:  saved={backfill.saved}  "
        f"failed={backfill.failed}  dry_run={backfill.dry_run}"
    )
    if not chunks:
        console.print("  Bundle:    [dim]skipped[/]")
        console.print("  Judge:     [dim]skipped[/]")
    else:
        succeeded = [c for c in chunks if c.judge_path is not None]
        failed = [c for c in chunks if c.error is not None]
        console.print(
            f"  Bundles:   {len(chunks)} chunk(s), "
            f"{sum(c.bundle_size for c in chunks)} session(s) total"
        )
        if failed:
            console.print(
                f"  Judge:     [green]{len(succeeded)} ok[/]  "
                f"[red]{len(failed)} failed[/]"
            )
            for c in failed:
                console.print(f"    [red]✗[/] {c.bundle_id}: {c.error}")
        else:
            console.print(f"  Judge:     [green]{len(succeeded)} ok[/]")
    if agg_path:
        console.print(f"  Aggregate: {agg_path}")
    else:
        console.print("  Aggregate: [dim]skipped[/]")
    console.print(f"  [dim]Total time: {duration:.1f}s[/]")


def _chunked(items: list[str], size: int) -> list[list[str]]:
    """Split ``items`` into fixed-size batches, last one may be shorter."""
    return [items[i:i + size] for i in range(0, len(items), size)]
