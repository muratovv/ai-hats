"""Session-end wrap-up nudge.

Called from ``auto_retro.make_decision()`` so results are folded into the
session-end banner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from ..paths import METRICS_JSON, session_dirname
from .window import (
    compute_session_end,
    parse_session_start,
    tasks_closed_in_window,
)


class WrapUpInfo(TypedDict):
    """Wrap-up nudge data, surfaced in the session-end banner (HATS-214)."""

    tasks_closed: int
    duration_min: int
    cache_read_mb: int


_WRAP_TASKS_THRESHOLD = 2
_WRAP_DURATION_MIN = 60


def evaluate_wrap_up(
    project_dir: Path, session_id: str
) -> WrapUpInfo | None:
    """Wrap-up nudge: fire when tasks_closed_in_window >= 2 AND duration > 60min.

    HATS-214. Source data:
      - duration_s from <runs_dir>/session_<id>/metrics.json
      - tasks_closed via window.tasks_closed_in_window (HATS-212 scope)
      - cache_read from metrics.json tokens block, rounded to MB
    Returns None when triggers not met or data unavailable. Never raises.
    """
    from ..paths import runs_dir

    sdir = runs_dir(project_dir) / session_dirname(session_id)
    metrics_path = sdir / METRICS_JSON
    if not metrics_path.exists():
        return None
    try:
        data = json.loads(metrics_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    duration_s_raw = data.get("duration_s") or 0
    try:
        duration_min = int(float(duration_s_raw) / 60)
    except (TypeError, ValueError):
        return None
    if duration_min <= _WRAP_DURATION_MIN:
        return None

    try:
        start = parse_session_start(session_id)
    except ValueError:
        return None
    end = compute_session_end(start, sdir, session_id)
    closed = tasks_closed_in_window(project_dir, start, end)
    if len(closed) < _WRAP_TASKS_THRESHOLD:
        return None

    cache_read = (data.get("tokens") or {}).get("cache_read") or 0
    try:
        cache_read_mb = int(int(cache_read) // 1_000_000)
    except (TypeError, ValueError):
        cache_read_mb = 0

    return WrapUpInfo(
        tasks_closed=len(closed),
        duration_min=duration_min,
        cache_read_mb=cache_read_mb,
    )
