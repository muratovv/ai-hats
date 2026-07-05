"""Tests for reminder.evaluate_wrap_up — HATS-214."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_hats.retro.reminder import evaluate_wrap_up
from ai_hats.state import TaskManager
from ai_hats.tracker_wiring import tracker_paths
from ai_hats.paths import runs_dir, state_md_path, tasks_dir


SESSION_ID = "20260101-120000-1"
SESSION_START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _setup_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (tasks_dir(project)).mkdir(parents=True)
    (state_md_path(project)).write_text("")
    (project / "ai-hats.yaml").write_text("task_prefix: TST\n")
    sdir = runs_dir(project) / f"session_{SESSION_ID}"
    sdir.mkdir(parents=True)
    return project


def _write_metrics(project: Path, **overrides) -> None:
    metrics = {
        "exit_code": 0,
        "turns": 10,
        "tool_calls": 50,
        "duration_s": 5400,  # 90 min — above threshold by default
        "tokens": {"cache_read": 12_500_000},  # 12 MB by default
    }
    metrics.update(overrides)
    (runs_dir(project) / f"session_{SESSION_ID}" / "metrics.json").write_text(
        json.dumps(metrics)
    )


def _create_done_task(
    project: Path, task_id: str, updated: datetime
) -> None:
    mgr = TaskManager(
        project, prefix="TST", strict_plan_check=False, layout=tracker_paths(project)
    )
    mgr.create_task(task_id, f"task {task_id}")
    # Mark as done by writing the yaml directly with the desired updated/state.
    # Going through transition() would require plan content, which the wrap-up
    # logic is independent of.
    task_path = tasks_dir(project) / task_id / "task.yaml"
    yaml_text = task_path.read_text()
    yaml_text = yaml_text.replace(
        "state: brainstorm", "state: done"
    )
    iso = updated.strftime("%Y-%m-%dT%H:%M:%SZ")
    yaml_text = yaml_text.replace(
        f"updated: '{yaml_text.split(chr(10))[0]}'",
        f"updated: '{iso}'",
    )
    # Replace the literal updated line robustly: regex substitute.
    import re

    yaml_text = re.sub(
        r"^updated:.*$", f"updated: '{iso}'", yaml_text, count=1, flags=re.MULTILINE
    )
    task_path.write_text(yaml_text)


def test_wrap_up_fires_when_thresholds_met(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project)
    _create_done_task(project, "TST-001", SESSION_START + timedelta(minutes=10))
    _create_done_task(project, "TST-002", SESSION_START + timedelta(minutes=30))
    _create_done_task(project, "TST-003", SESSION_START + timedelta(minutes=60))

    info = evaluate_wrap_up(project, SESSION_ID)
    assert info is not None
    assert info["tasks_closed"] == 3
    assert info["duration_min"] == 90
    assert info["cache_read_mb"] == 12


def test_wrap_up_below_tasks_threshold(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project)
    _create_done_task(project, "TST-001", SESSION_START + timedelta(minutes=10))

    assert evaluate_wrap_up(project, SESSION_ID) is None


def test_wrap_up_below_duration_threshold(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project, duration_s=1800)  # 30 min
    for i, off in enumerate([5, 10, 15], start=1):
        _create_done_task(
            project, f"TST-00{i}", SESSION_START + timedelta(minutes=off)
        )

    assert evaluate_wrap_up(project, SESSION_ID) is None


def test_wrap_up_no_metrics_returns_none(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    # No metrics.json written.
    _create_done_task(project, "TST-001", SESSION_START + timedelta(minutes=5))
    _create_done_task(project, "TST-002", SESSION_START + timedelta(minutes=10))
    assert evaluate_wrap_up(project, SESSION_ID) is None


def test_wrap_up_ignores_tasks_outside_window(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project)  # window 0..90min
    _create_done_task(
        project, "TST-001", SESSION_START + timedelta(hours=5)  # after end
    )
    _create_done_task(
        project, "TST-002", SESSION_START - timedelta(hours=5)  # before start
    )
    _create_done_task(
        project, "TST-003", SESSION_START + timedelta(hours=10)
    )

    assert evaluate_wrap_up(project, SESSION_ID) is None


def test_wrap_up_cache_read_rounds_to_mb(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project, tokens={"cache_read": 999_999})  # < 1 MB
    _create_done_task(project, "TST-001", SESSION_START + timedelta(minutes=5))
    _create_done_task(project, "TST-002", SESSION_START + timedelta(minutes=10))

    info = evaluate_wrap_up(project, SESSION_ID)
    assert info is not None
    assert info["cache_read_mb"] == 0


def test_wrap_up_zero_duration_returns_none(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_metrics(project, duration_s=0)
    _create_done_task(project, "TST-001", SESSION_START + timedelta(minutes=1))
    _create_done_task(project, "TST-002", SESSION_START + timedelta(minutes=2))

    assert evaluate_wrap_up(project, SESSION_ID) is None
