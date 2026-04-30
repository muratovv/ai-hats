"""Stale-retro reminder: nudge the user when skipped sessions accumulate.

Called from auto_retro.make_decision() so the result is folded into the
session-end banner. Reuses backfill.find_candidates() to count sessions in
a rolling window that don't yet have a retro file.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

from ..models import SessionRetroConfig
from .backfill import find_candidates


class ReminderInfo(TypedDict):
    """Structured stale-retro reminder data, surfaced in the session-end banner."""

    count: int
    since: str
    window_days: int
    parallel: int
    command: str


def evaluate(project_dir: Path, sr: SessionRetroConfig) -> tuple[ReminderInfo | None, str]:
    """Return (reminder_info, log_reason).

    `reminder_info` is None when no reminder should fire. `log_reason` is a
    short string suitable for retro.log so we can audit the decision.
    """
    if not sr.reminder.enabled:
        return None, "disabled"

    since = (date.today() - timedelta(days=sr.reminder.window_days)).isoformat()
    candidates, _ = find_candidates(project_dir, since=since)
    count = len(candidates)
    threshold = sr.reminder.max_skipped

    if count < threshold:
        return None, f"under threshold ({count}<{threshold} in {sr.reminder.window_days}d)"

    parallel = max(1, min(count, 4))
    info: ReminderInfo = {
        "count": count,
        "since": since,
        "window_days": sr.reminder.window_days,
        "parallel": parallel,
        "command": f"ai-hats retro --backfill --since {since} --parallel {parallel}",
    }
    return info, f"fired ({count}>={threshold} in {sr.reminder.window_days}d)"
