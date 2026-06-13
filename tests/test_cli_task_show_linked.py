"""HATS-691: `ai-hats task show <id>` renders linked-task context by default.

Owns the linked-context rendering semantics (HATS-745); the e2e gate keeps only
the wiring marker in the ``ai-hats task`` surface sweep
``tests/e2e/test_task_cli.py``. Default `show` appends the linked bodies;
`--short` restores the compact id/state/title index.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli.task import task
from ai_hats.models import TaskCard, TaskState
from ai_hats.paths import tasks_dir


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    tasks_dir(pd).mkdir(parents=True)
    monkeypatch.chdir(pd)
    return pd


def _write_card(pd: Path, card: TaskCard, plan_body: str | None = None) -> None:
    card_dir = tasks_dir(pd) / card.id
    card.save(card_dir / "task.yaml")
    if plan_body is not None:
        (card_dir / "plan.md").write_text(plan_body)


def _seed(pd: Path) -> None:
    _write_card(
        pd,
        TaskCard(
            id="HATS-900",
            title="Epic",
            state=TaskState.EXECUTE,
            description="EPIC DESCRIPTION BODY",
        ),
        plan_body="# EPIC PLAN\nEPIC PLAN BODY LINE",
    )
    _write_card(
        pd,
        TaskCard(id="HATS-901", title="Release", state=TaskState.DONE, description="RELEASE BODY"),
    )
    _write_card(
        pd,
        TaskCard(
            id="HATS-902",
            title="child",
            state=TaskState.EXECUTE,
            parent_task="HATS-900",
            related=["HATS-901"],
        ),
    )


def test_show_default_includes_linked_bodies(project_dir: Path):
    _seed(project_dir)
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert "Linked context:" in res.output
    assert "EPIC DESCRIPTION BODY" in res.output
    assert "EPIC PLAN BODY LINE" in res.output  # parent epic plan.md
    assert "RELEASE BODY" in res.output  # related card body
    # The literal relation tag survives (markup=False — not eaten as rich markup).
    assert "[parent_task]" in res.output


def test_show_short_omits_linked_bodies(project_dir: Path):
    _seed(project_dir)
    res = CliRunner().invoke(task, ["show", "HATS-902", "--short"])
    assert res.exit_code == 0, res.output
    assert "Linked context:" not in res.output
    assert "EPIC PLAN BODY LINE" not in res.output
    assert "RELEASE BODY" not in res.output
    # The compact link index is still present (current behaviour preserved).
    assert "HATS-901" in res.output
