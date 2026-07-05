"""Pin retro.window.tasks_closed_in_window against a seeded tracker (HATS-864).

The function swallows every exception (``except Exception: return []``), so a
miswired TaskManager construction inside it would silently empty retro facts
instead of failing — this test restores the loudness.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_hats.paths import state_md_path, tasks_dir
from ai_hats.retro.window import tasks_closed_in_window
from ai_hats.state import TaskManager
from ai_hats.tracker_wiring import tracker_paths

_UPDATED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seed_done_task(project: Path, task_id: str) -> None:
    mgr = TaskManager(
        project, prefix="TST", strict_plan_check=False, layout=tracker_paths(project)
    )
    mgr.create_task(task_id, f"task {task_id}")
    # Direct yaml edit: transition() would demand plan content the window
    # logic is independent of (same idiom as test_reminder_wrap_up).
    task_path = tasks_dir(project) / task_id / "task.yaml"
    text = task_path.read_text().replace("state: brainstorm", "state: done")
    iso = _UPDATED.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = re.sub(r"^updated:.*$", f"updated: '{iso}'", text, count=1, flags=re.MULTILINE)
    task_path.write_text(text)


def test_tasks_closed_in_window_finds_seeded_done_task(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    tasks_dir(project).mkdir(parents=True)
    state_md_path(project).write_text("")
    (project / "ai-hats.yaml").write_text("task_prefix: TST\n")
    _seed_done_task(project, "TST-1")

    closed = tasks_closed_in_window(
        project, _UPDATED - timedelta(hours=1), _UPDATED + timedelta(hours=1)
    )
    assert closed == ["TST-1"]


def test_tasks_closed_in_window_excludes_outside_window(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    tasks_dir(project).mkdir(parents=True)
    state_md_path(project).write_text("")
    (project / "ai-hats.yaml").write_text("task_prefix: TST\n")
    _seed_done_task(project, "TST-1")

    since = _UPDATED + timedelta(hours=2)
    assert tasks_closed_in_window(project, since, since + timedelta(hours=1)) == []
