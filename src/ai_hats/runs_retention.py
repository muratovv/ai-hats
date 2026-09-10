"""Age-bounded retention for ``sessions/runs/`` (HATS-1339, S5).

The run tree had no GC of any kind and grows monotonically in every consumer
project. Two tiers, and the unit of expiry is **one file inside a run dir** — a
run dir is never removed, so a session id never stops resolving:

* **facts** — ``audit.md``, ``metrics.json``, ``retro.log``, ``diagnostics.json``:
  retained forever. ``retro.facts`` links every retro to ``../runs/<sid>/audit.md``
  and reads ``metrics.json`` for the session window, and HATS-1374 is the
  precedent for silently blinding the reflect loop by trimming its inputs.
* **bulk** — the provider transcripts, the PTY trace, the composed prompt and the
  materialization/usage records: expire past :data:`BULK_MAX_AGE_DAYS`.

Structurally an allowlist, in the shape ``retired_dists`` uses to make pruning
something still needed *impossible rather than unlikely* (HATS-1280): a file is
removed only if its exact name is in the finite expirable set, from which the
retained set is subtracted at import. A name nobody here has heard of — a new
artifact a future writer adds, a sidecar, a hand-dropped note — matches neither
set and is therefore kept by default; the failure mode of forgetting to update
this module is unbounded disk, never lost evidence.

The bound is grounded on what actually reads these files rather than on taste:
``reflect session`` and ``reflect all`` touch the facts tier only, so no bound
can break them; the binding consumer is post-hoc forensics over the archive,
whose longest observed reach is this card's own V1 evidence (a window read 11
days after the fact). The bound is NOT what keeps a live session's own artifacts
safe, though — a long run's ``meta_prompt.txt`` is written once at start and never
touched again, so its mtime is the session's age and any bound is eventually
crossed while running. :func:`_session_is_live` is the guard; the bound only
decides how long a FINISHED session's bulk survives.

Leaf module: imports ``paths`` and observe's artifact-name schema and nothing
else, so both ``environment_recovery`` and ``observe`` can call it.
"""  # comment-length: allow

from __future__ import annotations

import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from ai_hats_observe.artifacts import (
    AUDIT_MD,
    META_PROMPT_TXT,
    METRICS_JSON,
    PTY_RAW_LOG,
    REASONING_LOG,
    RETRO_LOG,
    ROLE_MATERIALIZATION_JSON,
    SESSION_PREFIX,
    TRACE_LOG,
    TRANSCRIPT_JSONL,
    TRANSCRIPT_TXT,
    USAGE_JSON,
    session_start_dt,
)

from ai_hats_core.layout import ProjectLayout
from .session_liveness import LazyLiveness, session_owners

logger = logging.getLogger(__name__)

BULK_MAX_AGE_DAYS = 14
SWEEP_MIN_INTERVAL_HOURS = 6

#: Touched at the end of every sweep; its mtime is the only record kept.
STAMP_NAME = ".retention-sweep"

#: No shared constant exists — ``startup_notices`` writes the name as a literal.
DIAGNOSTICS_JSON = "diagnostics.json"

#: The facts tier. Never expired, at any age, for any reason.
RETAINED_ARTIFACTS: frozenset[str] = frozenset(
    {AUDIT_MD, METRICS_JSON, RETRO_LOG, DIAGNOSTICS_JSON}
)

#: The only names a sweep may ever unlink. Spelled through observe's constants so
#: a renamed artifact is an ImportError here, not a silently missed class.
EXPIRABLE_ARTIFACTS: frozenset[str] = (
    frozenset(
        {
            TRANSCRIPT_JSONL,
            TRACE_LOG,
            META_PROMPT_TXT,
            REASONING_LOG,
            ROLE_MATERIALIZATION_JSON,
            USAGE_JSON,
            TRANSCRIPT_TXT,
            PTY_RAW_LOG,
        }
    )
    - RETAINED_ARTIFACTS
)


@dataclass
class RetentionReport:
    """What one sweep did. Counts AND bytes — a cap nobody can see is a bug."""

    files_removed: int = 0
    bytes_removed: int = 0
    dirs_visited: int = 0
    errors: int = 0
    skipped: bool = False

    def summary(self) -> str:
        return (
            f"runs retention: dropped {self.files_removed} files / "
            f"{self.bytes_removed} bytes across {self.dirs_visited} run dirs "
            f"({self.errors} errors)"
        )


def sweep_runs(
    layout: ProjectLayout,
    *,
    max_age_days: int = BULK_MAX_AGE_DAYS,
    min_interval_hours: float = SWEEP_MIN_INTERVAL_HOURS,
    liveness: LazyLiveness | None = None,
) -> RetentionReport:
    """Expire bulk run artifacts of FINISHED sessions older than ``max_age_days``.

    Filesystem failures are logged and counted into ``errors`` rather than
    raised, so one unreadable run dir costs that dir and nothing else.

    Idempotent: a second call finds the tree already at its retained state and
    removes nothing. ``min_interval_hours=0`` forces the walk; otherwise a sweep
    that ran that recently short-circuits before opening a single run dir,
    because the caller is the per-run ``create_session`` chokepoint.
    """
    report = RetentionReport()
    root = layout.sessions.runs
    if not root.is_dir():
        return report

    stamp = root / STAMP_NAME
    if _swept_within(stamp, min_interval_hours):
        report.skipped = True
        return report

    cutoff = time.time() - max_age_days * 86400
    liveness = liveness or LazyLiveness()
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if _is_sweepable_run(entry, cutoff) and not _session_is_live(
                    layout, entry.name, liveness
                ):
                    _sweep_run_dir(Path(entry.path), cutoff, report)
    except OSError as exc:
        logger.warning("runs retention: cannot read %s: %s", root, exc)
        report.errors += 1

    _touch(stamp, report)
    if report.files_removed or report.errors:
        logger.warning("%s", report.summary())
    return report


def _swept_within(stamp: Path, min_interval_hours: float) -> bool:
    if min_interval_hours <= 0:
        return False
    try:
        last = stamp.stat().st_mtime
    except OSError:
        return False  # silent-ok: no readable stamp means "sweep now", the safe direction
    return (time.time() - last) < min_interval_hours * 3600


def _touch(stamp: Path, report: RetentionReport) -> None:
    try:
        stamp.touch()
    except OSError as exc:
        logger.warning("runs retention: cannot stamp %s: %s", stamp, exc)
        report.errors += 1


def _session_is_live(layout: ProjectLayout, run_name: str, liveness: LazyLiveness) -> bool:
    """Is the session that owns this run dir still going?

    The age bound alone is a guess with the same failure mode this card rejected
    for the caches: the longest observed session is 104.55 h and the trend is
    upward, and ``meta_prompt.txt`` is written once at START, so its mtime is the
    session's own age. A long enough run would have its composed prompt and launch
    record expired out from under it while live — the same on a forward clock jump.
    The owner anchor lives in the session's CACHE dir, and the sweep that reaps a
    dead owner's cache runs before this one in the same pass, so a cache dir still
    standing means live, or ownerless and inside its own TTL.
    """  # comment-length: allow — this gate is why the bound is not the only guard
    cache_dir = layout.cache.session(run_name[len(SESSION_PREFIX) :])
    if not cache_dir.is_dir():
        return False
    owners = session_owners(cache_dir)
    if not owners:
        return True  # unusable anchor (None) or unresolvable (()): never guess dead
    return any(liveness.is_live(pid, start_time) for pid, start_time in owners)


def _is_sweepable_run(entry: os.DirEntry[str], cutoff: float) -> bool:
    """A ``session_*`` dir that could hold something old enough to drop.

    The id-derived start time is a second, independent floor under every file's
    age: a run that STARTED after the cutoff has nothing expirable in it no
    matter what mtimes say, so a restored or copied-in file carrying a stale
    mtime cannot be mistaken for an aged one.
    """
    if not entry.name.startswith(SESSION_PREFIX):
        return False
    if not entry.is_dir(follow_symlinks=False):
        return False
    start = session_start_dt(entry.name)
    return start is None or start.timestamp() < cutoff


def _sweep_run_dir(run_dir: Path, cutoff: float, report: RetentionReport) -> None:
    try:
        with os.scandir(run_dir) as children:
            report.dirs_visited += 1
            for child in children:
                _expire(child, cutoff, report)
    except OSError as exc:
        logger.warning("runs retention: cannot read %s: %s", run_dir, exc)
        report.errors += 1


def _expire(child: os.DirEntry[str], cutoff: float, report: RetentionReport) -> None:
    if child.name not in EXPIRABLE_ARTIFACTS:
        return
    try:
        info = child.stat(follow_symlinks=False)
        # A symlink wearing a bulk artifact's name would resolve outside the run dir.
        if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff:
            return
        os.unlink(child.path)  # safe-delete: ok bulk artifact past retention
    except OSError as exc:
        logger.warning("runs retention: cannot drop %s: %s", child.path, exc)
        report.errors += 1
        return
    report.files_removed += 1
    report.bytes_removed += info.st_size


__all__ = [
    "BULK_MAX_AGE_DAYS",
    "EXPIRABLE_ARTIFACTS",
    "RETAINED_ARTIFACTS",
    "RetentionReport",
    "STAMP_NAME",
    "SWEEP_MIN_INTERVAL_HOURS",
    "sweep_runs",
]
