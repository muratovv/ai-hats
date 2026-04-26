"""Stale-retro reminder: nudge the user when skipped sessions accumulate.

Called from auto_retro.main() after the per-session decision is logged.
Reuses backfill.find_candidates() to count sessions in a rolling window
that don't yet have a retro file.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..models import SessionRetroConfig
from .backfill import find_candidates


def evaluate(project_dir: Path, sr: SessionRetroConfig) -> tuple[str | None, str]:
    """Return (reminder_text, log_reason).

    `reminder_text` is None when no reminder should fire. `log_reason` is a
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
    text = (
        f"[retro] {count} sessions without retro since {since} "
        f"(window: {sr.reminder.window_days}d).\n"
        f"[retro]   ai-hats retro --backfill --since {since} --parallel {parallel}"
    )
    return text, f"fired ({count}>={threshold} in {sr.reminder.window_days}d)"
