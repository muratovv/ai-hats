"""Shared session-window helpers used by builder and reminder.

HATS-212 introduced the [start_ts, end_ts] window for retro artifacts.
HATS-214 reuses the same logic for the wrap-up nudge — keep both consumers
in lockstep by living in one place.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import METRICS_JSON, PROJECT_CONFIG, strip_session_prefix

logger = logging.getLogger(__name__)


def parse_session_start(session_id: str) -> datetime:
    """Parse `YYYYMMDD-HHMMSS-N` (or `session_<id>`) into a UTC datetime."""
    sid = strip_session_prefix(session_id)
    try:
        return datetime.strptime(sid[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"Cannot parse session start from {session_id!r}") from e


def compute_session_end(
    session_start: datetime, session_dir: Path, session_id: str
) -> datetime:
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
        "session window upper bound: duration_s missing for %s, "
        "falling back to now(UTC)",
        session_id,
    )
    return datetime.now(timezone.utc)


def tasks_closed_in_window(
    project_dir: Path, since: datetime, until: datetime
) -> list[str]:
    """Return IDs of tasks whose `updated` falls in [since, until], state=done."""
    from ..paths import tasks_dir as _tasks_dir

    tasks_dir = _tasks_dir(project_dir)
    if not tasks_dir.exists():
        return []
    try:
        from ..models import ProjectConfig, TaskState
        from ai_hats_tracker.state import TaskManager
        from ..tracker_wiring import tracker_paths
    except ImportError:
        return []
    try:
        prefix = ProjectConfig.resolve_task_prefix(
            project_dir, project_dir / PROJECT_CONFIG
        )
        tm = TaskManager(
            project_dir,
            prefix=prefix,
            layout=tracker_paths(project_dir),
            strict_plan_check=False,
        )
        done = tm.list_tasks(state=TaskState.DONE)
    except Exception:
        return []
    closed: list[str] = []
    for t in done:
        ts = parse_task_timestamp(t.updated)
        if ts and since <= ts <= until:
            closed.append(t.id)
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


__all__ = [
    "compute_session_end",
    "parse_session_start",
    "parse_task_timestamp",
    "tasks_closed_in_window",
]
