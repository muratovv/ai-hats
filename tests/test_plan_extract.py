"""Tests for plan-extract — parser + CLI integration (HATS-231)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.models import TaskState
from ai_hats.plan_extract import extract_candidates, mark_extracted
from ai_hats.state import TaskManager


# ----------------------------- parser --------------------------------------


def test_parser_subtasks_block() -> None:
    text = (
        "# Plan\n\n"
        "## Subtasks\n"
        "- First sub\n"
        "- **Bold** second\n"
        "- `code` third\n"
        "\n## Other\n"
        "- ignored bullet\n"
    )
    cands = extract_candidates(text)
    assert [c.title for c in cands] == ["First sub", "Bold second", "code third"]
    assert all(c.kind == "subtasks" for c in cands)


def test_parser_steps_checklist() -> None:
    text = (
        "## Steps\n"
        "- [ ] Do thing one\n"
        "- [x] Already done\n"
        "- [ ] Do thing two\n"
    )
    cands = extract_candidates(text)
    titles = [c.title for c in cands]
    assert titles == ["Do thing one", "Already done", "Do thing two"]
    assert all(c.kind == "steps" for c in cands)


def test_parser_numbered_phase_headings() -> None:
    text = (
        "## Implementation\n"
        "### 1. Match logic\n"
        "Some prose.\n"
        "### 2. Execute guard\n"
        "### Phase 3: Cleanup\n"
        "### Step 4: Final\n"
        "### Random ignored\n"
    )
    cands = extract_candidates(text)
    titles = [c.title for c in cands]
    assert titles == ["Match logic", "Execute guard", "Cleanup", "Final"]
    assert all(c.kind == "phase" for c in cands)


def test_parser_skips_already_marked_lines() -> None:
    text = (
        "## Subtasks\n"
        "- First <!-- HATS-001 -->\n"
        "- Second\n"
    )
    cands = extract_candidates(text)
    assert [c.title for c in cands] == ["Second"]


def test_parser_priority_subtasks_over_steps() -> None:
    text = (
        "## Subtasks\n"
        "- only this should be returned\n"
        "## Steps\n"
        "- [ ] not picked up\n"
    )
    cands = extract_candidates(text)
    assert [c.title for c in cands] == ["only this should be returned"]


def test_parser_returns_empty_on_unstructured_plan() -> None:
    text = "# Plan\n\nJust some prose with no recognised structure.\n"
    assert extract_candidates(text) == []


def test_parser_truncates_long_title() -> None:
    long = "a" * 200
    text = f"## Subtasks\n- {long}\n"
    cands = extract_candidates(text)
    assert len(cands) == 1
    assert cands[0].title.endswith("…")
    assert len(cands[0].title) <= 81  # 80 chars + ellipsis


def test_mark_extracted_appends_marker_idempotent() -> None:
    text = "## Subtasks\n- First\n- Second\n"
    out = mark_extracted(text, line_no=1, child_id="HATS-099")
    assert "- First <!-- HATS-099 -->\n" in out
    # Idempotent
    out2 = mark_extracted(out, line_no=1, child_id="HATS-100")
    assert out2 == out


# --------------------------- CLI integration -------------------------------


def _setup_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project,
        check=True,
    )
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    (project / "ai-hats.yaml").write_text("task_prefix: TST\n")
    return project


def _seed_task_with_plan(project: Path, task_id: str, plan_body: str) -> Path:
    mgr = TaskManager(project, prefix="TST", strict_plan_check=False)
    mgr.create_task(task_id, "parent task")
    mgr.transition(task_id, TaskState.PLAN)
    plan = project / ".agent" / "backlog" / "tasks" / task_id / "plan.md"
    plan.write_text(plan_body)
    return plan


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_plan_extract_dry_run_does_not_create(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    plan_body = "## Subtasks\n- One\n- Two\n"
    _seed_task_with_plan(project, "TST-001", plan_body)
    monkeypatch.chdir(project)

    result = runner.invoke(main, ["task", "plan-extract", "TST-001", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "One" in result.output and "Two" in result.output

    mgr = TaskManager(project, prefix="TST", strict_plan_check=False)
    children = [t for t in mgr.list_tasks() if t.parent_task == "TST-001"]
    assert children == []


def test_plan_extract_auto_creates_all_and_marks(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    plan_body = "## Subtasks\n- Alpha task\n- Beta task\n"
    plan = _seed_task_with_plan(project, "TST-001", plan_body)
    monkeypatch.chdir(project)

    result = runner.invoke(main, ["task", "plan-extract", "TST-001", "--auto"])
    assert result.exit_code == 0, result.output

    mgr = TaskManager(project, prefix="TST", strict_plan_check=False)
    children = sorted(
        (t for t in mgr.list_tasks() if t.parent_task == "TST-001"),
        key=lambda t: t.id,
    )
    titles = [t.title for t in children]
    assert "Alpha task" in titles
    assert "Beta task" in titles
    assert all("extracted-from-plan" in t.tags for t in children)

    marked_text = plan.read_text()
    assert marked_text.count("<!-- TST-") == 2

    # Re-run is no-op (everything already marked).
    result2 = runner.invoke(main, ["task", "plan-extract", "TST-001", "--auto"])
    assert result2.exit_code == 0, result2.output
    children_after = [t for t in mgr.list_tasks() if t.parent_task == "TST-001"]
    assert len(children_after) == 2  # unchanged


def test_plan_extract_json_output(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    plan_body = "## Steps\n- [ ] First\n- [ ] Second\n"
    _seed_task_with_plan(project, "TST-001", plan_body)
    monkeypatch.chdir(project)

    result = runner.invoke(main, ["task", "plan-extract", "TST-001", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])
    assert [c["title"] for c in data] == ["First", "Second"]
    assert all(c["kind"] == "steps" for c in data)


def test_plan_extract_missing_plan_exits_nonzero(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    mgr = TaskManager(project, prefix="TST", strict_plan_check=False)
    mgr.create_task("TST-001", "no plan")
    # No transition → no plan.md
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["task", "plan-extract", "TST-001"])
    assert result.exit_code != 0
    assert "plan.md not found" in result.output


def test_plan_extract_empty_scaffold_exits_nonzero(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    mgr = TaskManager(project, prefix="TST", strict_plan_check=False)
    mgr.create_task("TST-001", "scaffold only")
    mgr.transition("TST-001", TaskState.PLAN)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["task", "plan-extract", "TST-001"])
    assert result.exit_code == 2
    assert "scaffold" in result.output.lower()


def test_plan_extract_no_candidates(
    tmp_path: Path, runner: CliRunner, monkeypatch
) -> None:
    project = _setup_project(tmp_path)
    plan_body = "# Plan\n\nJust prose, no structure.\n"
    _seed_task_with_plan(project, "TST-001", plan_body)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["task", "plan-extract", "TST-001"])
    assert result.exit_code == 0
    assert "No candidates" in result.output
