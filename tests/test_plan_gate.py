"""Unit tests for the per-section plan gate (HATS-635).

Covers `TaskManager._unfilled_sections` in isolation — no subprocess, no git.
The gate's integration behaviour (transition plan→execute) lives in
`tests/test_plan_sync.py`; the real-binary path in `tests/e2e/`.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from ai_hats.state import PLAN_SCAFFOLD, PLAN_SECTIONS, TaskManager


@pytest.fixture
def mgr(tmp_path: Path) -> TaskManager:
    project = tmp_path / "project"
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    return TaskManager(project, strict_plan_check=False)


def _write_plan(mgr: TaskManager, body: str) -> object:
    task = mgr.create_task("HATS-001", "Probe")
    plan_path = mgr.tasks_dir / task.id / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(body)
    return task


ALL_REQUIRED = [s.name for s in PLAN_SECTIONS if s.required]


def test_all_sections_filled_returns_empty(mgr: TaskManager) -> None:
    task = _write_plan(
        mgr,
        "# Plan for HATS-001: Probe\n\n"
        "## Requirements\nShip the gate.\n\n"
        "## Scope & Out-of-scope\nIn: gate. Out: skill.\n\n"
        "## Steps\n- [x] do thing\n\n"
        "## Verification Protocol\npytest -q\n",
    )
    assert mgr._unfilled_sections(task) == []


def test_blank_scaffold_flags_every_required_section(mgr: TaskManager) -> None:
    task = _write_plan(
        mgr, PLAN_SCAFFOLD.format(task_id="HATS-001", title="Probe")
    )
    assert mgr._unfilled_sections(task) == ALL_REQUIRED


def test_partial_plan_names_only_the_empty_sections(mgr: TaskManager) -> None:
    task = _write_plan(
        mgr,
        "# Plan for HATS-001: Probe\n\n"
        "## Requirements\nOnly this one is filled.\n\n"
        "## Scope & Out-of-scope\n\n"
        "## Steps\n\n"
        "## Verification Protocol\n\n",
    )
    assert mgr._unfilled_sections(task) == [
        "Scope & Out-of-scope",
        "Steps",
        "Verification Protocol",
    ]


def test_whitespace_only_body_counts_as_unfilled(mgr: TaskManager) -> None:
    task = _write_plan(
        mgr,
        "# Plan for HATS-001: Probe\n\n"
        "## Requirements\n   \n\t\n\n"
        "## Scope & Out-of-scope\nx\n\n"
        "## Steps\nx\n\n"
        "## Verification Protocol\nx\n",
    )
    assert mgr._unfilled_sections(task) == ["Requirements"]


def test_missing_heading_counts_as_unfilled(mgr: TaskManager) -> None:
    # Free-form plan with no section headings at all — the old byte-equality
    # check passed this ("not the verbatim scaffold"); the per-section gate
    # must flag every required section.
    task = _write_plan(mgr, "# Plan\n\nsome free text, no sections\n")
    assert mgr._unfilled_sections(task) == ALL_REQUIRED


def test_subheadings_inside_a_section_do_not_break_fill(mgr: TaskManager) -> None:
    # A level-3 heading inside a section is body content, not a new section.
    task = _write_plan(
        mgr,
        "# Plan for HATS-001: Probe\n\n"
        "## Requirements\n### detail\nnested content\n\n"
        "## Scope & Out-of-scope\nx\n\n"
        "## Steps\nx\n\n"
        "## Verification Protocol\nx\n",
    )
    assert mgr._unfilled_sections(task) == []


def test_missing_plan_file_flags_every_required_section(mgr: TaskManager) -> None:
    task = mgr.create_task("HATS-001", "Probe")
    plan_path = mgr.tasks_dir / task.id / "plan.md"
    if plan_path.exists():
        plan_path.unlink()
    assert mgr._unfilled_sections(task) == ALL_REQUIRED
