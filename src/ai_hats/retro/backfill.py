"""Batch retro generation for sessions without a retro.

Automates what HATS-159 investigation did by hand: walk `.gitlog/session_*/`,
filter out legitimate-skip sessions, and run `SessionRetroBuilder` for each
remaining candidate.

Filtering is intentionally conservative — the same role/threshold exclusions
that the shell hook and should_run() enforce apply here too, so backfill
never generates retros for sessions auto-retro would have skipped.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .auto_retro import write_retro_log
from .builder import BuilderMode, SessionRetroBuilder
from .llm_caller import SubprocessLLMCaller


EXCLUDED_ROLES = {"judge", "test-agent", "", None}


@dataclass
class Candidate:
    """One session eligible for backfill."""
    session_id: str
    session_dir: Path
    turns: int
    tool_calls: int
    role: str | None


@dataclass
class SkippedSession:
    session_id: str
    reason: str


@dataclass
class BackfillResult:
    """Outcome of running backfill on a single session."""
    session_id: str
    status: str          # "saved" | "failed" | "dry_run"
    detail: str          # path on saved, exception repr on failed
    duration_s: float = 0.0


@dataclass
class BackfillSummary:
    total_candidates: int = 0
    saved: int = 0
    failed: int = 0
    dry_run: int = 0
    pre_filter_skipped: list[SkippedSession] = field(default_factory=list)
    results: list[BackfillResult] = field(default_factory=list)
    total_duration_s: float = 0.0
    interrupted: bool = False


def find_candidates(
    project_dir: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    min_turns: int = 1,
    only: list[str] | None = None,
    force: bool = False,
    mode: BuilderMode = BuilderMode.PROGRAMMATIC,
) -> tuple[list[Candidate], list[SkippedSession]]:
    """Return (candidates, skipped) for backfill.

    `candidates` is what will be processed (in chronological order).
    `skipped` records sessions filtered out with the reason, so the CLI
    can summarise "why nothing matched".

    `since` is inclusive, `until` is exclusive — together they form the
    half-open window ``[since, until)`` over session dates parsed from the
    first 8 chars of the session id. This makes consecutive runs
    ``--until X`` then ``--since X --until Y`` cover disjoint adjacent
    intervals without overlap or gap.
    """
    gitlog = project_dir / ".gitlog"
    if not gitlog.is_dir():
        return [], []

    retros_dir = project_dir / ".agent" / "retrospectives" / "sessions"
    only_set = set(only) if only else None

    candidates: list[Candidate] = []
    skipped: list[SkippedSession] = []

    session_dirs = sorted(
        (p for p in gitlog.iterdir() if p.is_dir() and p.name.startswith("session_")),
        key=lambda p: p.name,
    )

    for sd in session_dirs:
        sid = sd.name.removeprefix("session_")

        if only_set is not None and sid not in only_set:
            continue

        if since and sid[:8] < since.replace("-", ""):
            skipped.append(SkippedSession(sid, f"before --since {since}"))
            continue

        if until and sid[:8] >= until.replace("-", ""):
            skipped.append(SkippedSession(sid, f"on or after --until {until}"))
            continue

        if not force:
            existing = (
                (retros_dir / mode.value / f"{sid}.md").exists()
                or (retros_dir / "llm" / f"{sid}.md").exists()
                or (retros_dir / "programmatic" / f"{sid}.md").exists()
            )
            if existing:
                skipped.append(SkippedSession(sid, "retro already exists (use --force)"))
                continue

        metrics_path = sd / "metrics.json"
        if not metrics_path.exists():
            skipped.append(SkippedSession(sid, "metrics.json missing"))
            continue

        try:
            m = json.loads(metrics_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            skipped.append(SkippedSession(sid, f"metrics.json unreadable: {exc}"))
            continue

        role = m.get("role")
        if role in EXCLUDED_ROLES:
            skipped.append(SkippedSession(sid, f"role={role or 'none'} (excluded)"))
            continue

        turns = int(m.get("turns", 0) or 0)
        tool_calls = int(m.get("tool_calls", 0) or 0)
        if turns < min_turns and tool_calls == 0:
            skipped.append(SkippedSession(sid, f"turns={turns}<{min_turns}, tool_calls=0"))
            continue

        candidates.append(Candidate(
            session_id=sid, session_dir=sd,
            turns=turns, tool_calls=tool_calls, role=role,
        ))

    return candidates, skipped


def backfill_one(
    project_dir: Path,
    candidate: Candidate,
    *,
    mode: BuilderMode,
    timeout: int,
    dry_run: bool,
) -> BackfillResult:
    """Run the builder for one candidate. Never raises — errors → status='failed'."""
    sid = candidate.session_id
    if dry_run:
        write_retro_log(project_dir, sid, "backfill", "dry_run", f"mode={mode.value}")
        return BackfillResult(
            session_id=sid, status="dry_run",
            detail=f"would run mode={mode.value}",
        )

    write_retro_log(project_dir, sid, "backfill", "start", f"mode={mode.value}")
    start = time.monotonic()
    try:
        llm_caller = (
            SubprocessLLMCaller(project_dir, timeout=timeout)
            if mode == BuilderMode.LLM else None
        )
        builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)
        path = builder.build_and_save(sid, mode=mode)
        dur = time.monotonic() - start
        write_retro_log(project_dir, sid, "backfill", "saved", str(path))
        return BackfillResult(
            session_id=sid, status="saved", detail=str(path), duration_s=dur,
        )
    except Exception as exc:
        dur = time.monotonic() - start
        write_retro_log(project_dir, sid, "backfill", "failed", repr(exc))
        return BackfillResult(
            session_id=sid, status="failed", detail=repr(exc), duration_s=dur,
        )


def run_backfill(
    project_dir: Path,
    *,
    mode: BuilderMode = BuilderMode.PROGRAMMATIC,
    since: str | None = None,
    until: str | None = None,
    min_turns: int = 1,
    only: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = 600,
    parallel: int = 1,
    printer: Callable[[str], None] = print,
) -> BackfillSummary:
    """Top-level orchestrator.

    `printer` receives per-session progress lines so the CLI can format
    them with colours while tests capture plain strings.

    With ``parallel > 1``, candidates are processed concurrently via a
    ThreadPoolExecutor (HATS-167). Order of progress lines is completion
    order, not candidate order — each line carries its own ``[idx/N]``
    prefix (the original position) so the reader can reconstruct.
    Keyboard interrupt cancels pending futures and returns partial results.
    """
    candidates, pre_skipped = find_candidates(
        project_dir,
        since=since, until=until, min_turns=min_turns,
        only=only, force=force, mode=mode,
    )
    summary = BackfillSummary(
        total_candidates=len(candidates),
        pre_filter_skipped=pre_skipped,
    )

    if not candidates:
        return summary

    start_all = time.monotonic()
    total = len(candidates)

    def _record(res: BackfillResult, idx: int, cand: Candidate) -> None:
        prefix = f"[{idx}/{total}] {cand.session_id}"
        meta = f"turns={cand.turns} tool_calls={cand.tool_calls}"
        summary.results.append(res)
        if res.status == "saved":
            summary.saved += 1
            printer(f"{prefix}  {meta}  → saved in {res.duration_s:.1f}s")
        elif res.status == "failed":
            summary.failed += 1
            printer(f"{prefix}  {meta}  → FAILED ({res.detail})")
        else:  # dry_run
            summary.dry_run += 1
            printer(f"{prefix}  {meta}  → dry-run (mode={mode.value})")

    if parallel <= 1:
        try:
            for idx, cand in enumerate(candidates, start=1):
                res = backfill_one(
                    project_dir, cand, mode=mode, timeout=timeout, dry_run=dry_run,
                )
                _record(res, idx, cand)
        except KeyboardInterrupt:
            summary.interrupted = True
    else:
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
            futures: dict[cf.Future, tuple[int, Candidate]] = {
                ex.submit(
                    backfill_one, project_dir, cand,
                    mode=mode, timeout=timeout, dry_run=dry_run,
                ): (idx, cand)
                for idx, cand in enumerate(candidates, start=1)
            }
            try:
                for fut in cf.as_completed(futures):
                    idx, cand = futures[fut]
                    # backfill_one catches exceptions internally and returns
                    # a failed BackfillResult; .result() should not raise.
                    # Guard anyway so a rogue exception in the future's own
                    # machinery doesn't take down the whole batch.
                    try:
                        res = fut.result()
                    except Exception as exc:  # pragma: no cover — defensive
                        res = BackfillResult(
                            session_id=cand.session_id,
                            status="failed", detail=repr(exc),
                        )
                    _record(res, idx, cand)
            except KeyboardInterrupt:
                summary.interrupted = True
                ex.shutdown(wait=False, cancel_futures=True)

    summary.total_duration_s = time.monotonic() - start_all
    return summary
