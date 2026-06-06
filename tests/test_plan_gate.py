"""Unit tests for the per-section plan gate (HATS-635).

Covers `TaskManager._unfilled_sections` in isolation — no subprocess, no git.
The gate's integration behaviour (transition plan→execute) lives in
`tests/test_plan_scaffold.py`; the real-binary path in `tests/e2e/`.
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
    task, _ = mgr.create_task("HATS-001", "Probe")
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
    task, _ = mgr.create_task("HATS-001", "Probe")
    plan_path = mgr.tasks_dir / task.id / "plan.md"
    if plan_path.exists():
        plan_path.unlink()
    assert mgr._unfilled_sections(task) == ALL_REQUIRED


# --- conditional "Approach & counter" value-counter stage (HATS-621) -------
# The devils-advocate stage is scaffolded and skill-routed but `required=False`:
# the gate never blocks `execute` on it. An empty Approach & counter must NOT
# appear in `_unfilled_sections` — the "non-trivial plans fill it" norm is
# behavioural, not engine-enforced.


def test_approach_and_counter_is_a_conditional_section() -> None:
    by_name = {s.name: s for s in PLAN_SECTIONS}
    assert "Approach & counter" in by_name, (
        "M3 (HATS-621) must add the 'Approach & counter' section to PLAN_SECTIONS"
    )
    assert by_name["Approach & counter"].required is False, (
        "'Approach & counter' must be conditional (required=False) — it never "
        "blocks the gate; the fill-or-N/A norm is behavioural"
    )


def test_approach_and_counter_sits_after_requirements_before_scope() -> None:
    # Order C (the agreed flow: interview ⇄ devils-advocate → design-minimalism).
    names = [s.name for s in PLAN_SECTIONS]
    assert names.index("Approach & counter") == names.index("Requirements") + 1
    assert names.index("Approach & counter") < names.index("Scope & Out-of-scope")


def test_blank_scaffold_does_not_flag_the_optional_section(mgr: TaskManager) -> None:
    task = _write_plan(
        mgr, PLAN_SCAFFOLD.format(task_id="HATS-001", title="Probe")
    )
    # The scaffold carries an empty `## Approach & counter`, but it is optional.
    assert "Approach & counter" not in mgr._unfilled_sections(task)


def test_all_required_filled_with_empty_optional_passes(mgr: TaskManager) -> None:
    # Every required section filled; Approach & counter present but empty.
    task = _write_plan(
        mgr,
        "# Plan for HATS-001: Probe\n\n"
        "## Requirements\nShip the value-counter stage.\n\n"
        "## Approach & counter\n\n"
        "## Scope & Out-of-scope\nIn: section. Out: role.\n\n"
        "## Steps\n- [x] do thing\n\n"
        "## Verification Protocol\npytest -q\n",
    )
    assert mgr._unfilled_sections(task) == []


# --- plan-gate orchestrator ↔ engine sync (HATS-636) ----------------------
# The `plan-gate` skill is a table of contents: its section→skill table must
# list exactly the engine's PLAN_SECTIONS, in order. If the engine adds /
# renames / reorders a section without the skill following (or vice-versa),
# the gate's documented contract drifts from what it enforces. Fail loudly.

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_GATE_SKILL = REPO_ROOT / "library" / "core" / "skills" / "plan-gate" / "SKILL.md"


def _plan_gate_section_names() -> list[str]:
    """First-column entries of the plan-gate section→skill markdown table."""
    names: list[str] = []
    in_table = False
    for line in PLAN_GATE_SKILL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("| Plan section"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            break  # table ended
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue  # separator row (|---|---|)
        names.append(cells[0])
    return names


def test_plan_gate_skill_table_matches_engine_sections() -> None:
    engine = [s.name for s in PLAN_SECTIONS]
    assert engine, "positive control: PLAN_SECTIONS must be non-empty"

    skill = _plan_gate_section_names()
    assert skill, (
        "parser found no rows in the plan-gate section table — check the "
        f"table header/format in {PLAN_GATE_SKILL}"
    )
    assert skill == engine, (
        "plan-gate SKILL.md section table drifted from engine PLAN_SECTIONS.\n"
        f"  engine: {engine}\n  skill:  {skill}"
    )
