"""Shared session-window helpers used by builder and reminder.

HATS-212 introduced the [start_ts, end_ts] window for retro artifacts.
HATS-214 reuses the same logic for the wrap-up nudge — keep both consumers
in lockstep by living in one place.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_hats_observe.artifacts import METRICS_JSON, session_dirname, strip_session_prefix

logger = logging.getLogger(__name__)


def parse_session_start(session_id: str) -> datetime:
    """Parse `YYYYMMDD-HHMMSS-N-PID` (or `session_<id>`) into a UTC datetime.

    Kept independent of ``ai_hats_observe.artifacts.session_start_dt`` — see
    ``paths._discovery.session_start_ts`` for why the integrator cannot import
    a symbol newer than observe's published version (HATS-1248).
    """
    sid = strip_session_prefix(session_id)
    try:
        return datetime.strptime(sid[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"Cannot parse session start from {session_id!r}") from e


def compute_session_end(session_start: datetime, session_dir: Path, session_id: str) -> datetime:
    """Read metrics.json:duration_s; fall back to now(UTC) with a log line.

    The window upper bound matters: without it artifacts and tasks_closed
    leak into repo-wide history (HATS-212).
    """
    metrics_path = session_dir / METRICS_JSON
    if metrics_path.exists():
        try:
            data = json.loads(metrics_path.read_text())
            duration_s = data.get("duration_s")
            if duration_s is not None and float(duration_s) > 0:
                return session_start + timedelta(seconds=float(duration_s))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    logger.info(
        "session window upper bound: duration_s missing for %s, falling back to now(UTC)",
        session_id,
    )
    return datetime.now(timezone.utc)


def tasks_closed_in_window(project_dir: Path, since: datetime, until: datetime) -> list[str]:
    """Return IDs of tasks whose `completed_at` falls in [since, until], state=done.

    Loud by design (HATS-1259): a read that cannot be performed raises rather than
    reporting "nothing closed". The wrap-up nudge tolerates that at the UX boundary
    (``auto_retro.make_decision``); ``session retro`` should not.
    """
    from ..rack_workspace import closed_tasks

    closed: list[str] = []
    for task in closed_tasks(project_dir):
        ts = parse_task_timestamp(task.completed_at)
        if ts is None:
            logger.warning(
                "task %s is done with no completed_at stamp — not counted in the session window",
                task.id,
            )
            continue
        if since <= ts <= until:
            closed.append(task.id)
    return sorted(closed)


def parse_task_timestamp(value: str) -> datetime | None:
    """Accept ISO-8601 (with/without trailing Z) or date-only YYYY-MM-DD."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def session_cut(layout: ProjectLayout, session_id: str) -> datetime:
    """Upper bound of what existed for a session: start + duration_s, or end of start day when duration_s is absent.

    Distinct from ``compute_session_end``: its fallback is ``now()``, which (a) fails to truncate on historic runs
    and (b) gives runner and inbox-validator different candidate sets (HATS-1445).
    Unparseable session IDs return datetime.max (fail-open: retain all cards).
    """

    sid = strip_session_prefix(session_id)
    try:
        start = parse_session_start(sid)
    except ValueError as e:
        logger.info("session_cut: unparseable session start for %s (%s)", session_id, e)
        return datetime.max.replace(tzinfo=timezone.utc)

    metrics_path = layout.sessions.runs / session_dirname(sid) / METRICS_JSON
    if metrics_path.exists():
        try:
            data = json.loads(metrics_path.read_text())
            duration_s = data.get("duration_s")
            if duration_s is not None and float(duration_s) > 0:
                return start + timedelta(seconds=float(duration_s))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.info("session_cut: metrics.json unreadable/invalid for %s (%s)", session_id, e)
    return start.replace(hour=23, minute=59, second=59, microsecond=0)


__all__ = [
    "compute_session_end",
    "parse_session_start",
    "parse_task_timestamp",
    "session_cut",
    "tasks_closed_in_window",
]
