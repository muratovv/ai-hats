"""Tracker + root-class layout migration: `.agent/{backlog,hypotheses,decisions,STATE.md,.last_backup}` → `<dir>/...` (HATS-313)."""

from __future__ import annotations

from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.paths import (
    backlog_dir,
    decisions_dir,
    hypotheses_flat_dir,
    last_backup_path,
    proposals_dir,
    state_md_path,
    tasks_dir,
)
from ai_hats.paths import PROJECT_CONFIG


def _seed_tracker_legacy(project_dir: Path) -> dict[str, Path]:
    (project_dir / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    seeds: dict[str, Path] = {}
    # Tasks
    td = project_dir / ".agent" / "backlog" / "tasks" / "HATS-001"
    td.mkdir(parents=True)
    (td / "task.yaml").write_text("id: HATS-001\ntitle: x\n")
    seeds["task"] = td / "task.yaml"
    # Proposals
    pd = project_dir / ".agent" / "backlog" / "proposals"
    pd.mkdir(parents=True)
    (pd / "PROP-001.yaml").write_text("id: PROP-001\n")
    seeds["proposal"] = pd / "PROP-001.yaml"
    # Hypotheses
    hd = project_dir / ".agent" / "hypotheses"
    hd.mkdir(parents=True)
    (hd / "HYP-001.yaml").write_text("id: HYP-001\n")
    seeds["hypothesis"] = hd / "HYP-001.yaml"
    # Decisions
    dd = project_dir / ".agent" / "decisions"
    dd.mkdir(parents=True)
    (dd / "2026-01-01-adr.md").write_text("decision")
    seeds["decision"] = dd / "2026-01-01-adr.md"
    # STATE.md + .last_backup
    (project_dir / ".agent" / "STATE.md").write_text("# state")
    seeds["state_md"] = project_dir / ".agent" / "STATE.md"
    (project_dir / ".agent" / ".last_backup").write_text("/tmp/backup-1")
    seeds["last_backup"] = project_dir / ".agent" / ".last_backup"
    return seeds


def test_tracker_migration_moves_all_paths(tmp_path: Path) -> None:
    _seed_tracker_legacy(tmp_path)
    asm = Assembler(tmp_path)

    asm._migrate_layout_v4_tracker()

    # Task / proposal / hypothesis / decision under new layout
    assert (tasks_dir(tmp_path) / "HATS-001" / "task.yaml").exists()
    assert (proposals_dir(tmp_path) / "PROP-001.yaml").exists()
    # v4 layout migration lands flat HYP files at the legacy flat dir; the rack
    # dir-per-card normalization to tracker/backlog/hypotheses is a later step (HATS-1054).
    assert (hypotheses_flat_dir(tmp_path) / "HYP-001.yaml").exists()
    assert (decisions_dir(tmp_path) / "2026-01-01-adr.md").exists()
    # STATE.md + .last_backup at dir root
    assert state_md_path(tmp_path).read_text() == "# state"
    assert last_backup_path(tmp_path).read_text() == "/tmp/backup-1"
    # Legacy paths gone
    assert not (tmp_path / ".agent" / "backlog").exists()
    assert not (tmp_path / ".agent" / "hypotheses").exists()
    assert not (tmp_path / ".agent" / "decisions").exists()
    assert not (tmp_path / ".agent" / "STATE.md").exists()
    assert not (tmp_path / ".agent" / ".last_backup").exists()


def test_tracker_migration_idempotent(tmp_path: Path) -> None:
    _seed_tracker_legacy(tmp_path)
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_tracker()
    before_state = state_md_path(tmp_path).read_text()
    asm._migrate_layout_v4_tracker()  # no-op
    assert state_md_path(tmp_path).read_text() == before_state
    assert (tasks_dir(tmp_path) / "HATS-001" / "task.yaml").exists()


def test_tracker_migration_e2e_task_visible(tmp_path: Path) -> None:
    """End-to-end: after migration, TaskManager finds the task via new paths."""
    from ai_hats_tracker.state import TaskManager
    from ai_hats.tracker_wiring import tracker_paths

    _seed_tracker_legacy(tmp_path)
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_tracker()

    mgr = TaskManager(tmp_path, prefix="HATS", layout=tracker_paths(tmp_path))
    task = mgr.get_task("HATS-001")
    assert task is not None
    assert task.title == "x"


def test_tracker_migration_noop_on_clean_project(tmp_path: Path) -> None:
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    asm = Assembler(tmp_path)
    asm._migrate_layout_v4_tracker()  # must not raise
    assert not backlog_dir(tmp_path).exists() or not any(backlog_dir(tmp_path).iterdir())
