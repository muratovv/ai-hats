"""End-to-end lifecycle smoke through the rack CLI + declared kit (HATS-1043).

Drives create → plan → execute → document → review → done → reopen → done on a
TEMP catalog via CliRunner, asserting the packaged declaration-bound kit behaves:
plan-scaffold writes plan.md on `plan`; plan-gate blocks `execute` on empty
sections and passes on filled; stamp-lifecycle stamps `completed_at` on `done`;
the reopen edge clears it (logging "Reopened from done") with plan-gate skipped;
and the stamp is re-applied on the second `done`.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.models import TaskCard

_FILLED_PLAN = (
    "# Plan\n\n## Requirements\nr\n\n## Scope & Out-of-scope\ns\n\n"
    "## Steps\nx\n\n## Verification Protocol\nv\n"
)


def _card(tasks_dir: Path) -> TaskCard:
    return TaskCard.from_yaml(tasks_dir / "HATS-001" / "task.yaml")


def test_full_lifecycle_through_the_declared_kit(tmp_path):
    runner = CliRunner()
    tasks_dir = tmp_path / "tasks"
    args = ["--tasks-dir", str(tasks_dir)]
    plan_md = tasks_dir / "HATS-001" / "plan.md"

    assert runner.invoke(main, ["create", "demo", *args]).exit_code == 0
    assert _card(tasks_dir).state == "brainstorm"

    # plan-scaffold writes plan.md on entering `plan`
    assert runner.invoke(main, ["transition", "HATS-001", "plan", *args]).exit_code == 0
    assert plan_md.is_file() and "## Requirements" in plan_md.read_text()

    # plan-gate blocks `execute` on the empty scaffold
    blocked = runner.invoke(main, ["transition", "HATS-001", "execute", *args])
    assert blocked.exit_code != 0
    assert "Requirements" in blocked.output
    assert _card(tasks_dir).state == "plan"  # nothing persisted

    # gate passes once the sections are filled
    plan_md.write_text(_FILLED_PLAN)
    assert runner.invoke(main, ["transition", "HATS-001", "execute", *args]).exit_code == 0
    assert runner.invoke(main, ["transition", "HATS-001", "document", *args]).exit_code == 0
    assert runner.invoke(main, ["transition", "HATS-001", "review", *args]).exit_code == 0

    # stamp-lifecycle stamps completed_at on `done`
    assert runner.invoke(main, ["transition", "HATS-001", "done", *args]).exit_code == 0
    first_stamp = _card(tasks_dir).completed_at
    assert first_stamp

    # reopen: plan-gate is skipped on the done→execute edge (empty plan does NOT
    # block), completed_at is cleared, and the reopen note is logged.
    plan_md.write_text("")  # empty again — proves the gate is skipped, not passed
    reopened = runner.invoke(main, ["transition", "HATS-001", "execute", *args])
    assert reopened.exit_code == 0, reopened.output
    card = _card(tasks_dir)
    assert card.state == "execute"
    assert not card.completed_at  # cleared by clear-lifecycle
    assert any("Reopened from done" in str(e) for e in card.work_log)

    # stamp is re-applied on the second `done`
    assert runner.invoke(main, ["transition", "HATS-001", "document", *args]).exit_code == 0
    assert runner.invoke(main, ["transition", "HATS-001", "review", *args]).exit_code == 0
    assert runner.invoke(main, ["transition", "HATS-001", "done", *args]).exit_code == 0
    assert _card(tasks_dir).completed_at  # re-stamped
