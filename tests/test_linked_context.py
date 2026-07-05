"""HATS-691: the shared linked-context assembly module.

HATS-689's logic was extracted from ``SubAgentRunner`` into
``ai_hats.linked_context`` so ``ai-hats task show`` can reuse the same seam.
These tests pin the module-level API directly (the runner-level behaviour is
covered by ``test_runtime_linked_context.py``, which still passes — proving the
move is behaviour-preserving).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.linked_context import load_linked_context, load_ticket
from ai_hats.models import TaskCard, TaskState, WorkLogEntry
from ai_hats.paths import tasks_dir


def _write_card(project_dir: Path, card: TaskCard, plan_body: str | None = None) -> None:
    card_dir = tasks_dir(project_dir) / card.id
    card.save(card_dir / "task.yaml")
    if plan_body is not None:
        (card_dir / "plan.md").write_text(plan_body)


def test_load_linked_context_module_assembles_links(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-900",
            title="Epic",
            state=TaskState.EXECUTE,
            description="EPIC DESCRIPTION BODY",
            work_log=[
                WorkLogEntry(timestamp="2026-01-01T00:00:00Z", message="OLD ENTRY"),
                WorkLogEntry(timestamp="2026-02-02T00:00:00Z", message="LATEST ENTRY"),
            ],
        ),
        plan_body="# EPIC PLAN\nEPIC PLAN BODY",
    )
    _write_card(
        project_dir,
        TaskCard(id="HATS-901", title="Release", state=TaskState.DONE, description="RELEASE BODY"),
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-902",
            title="child",
            state=TaskState.EXECUTE,
            parent_task="HATS-900",
            related=["HATS-901"],
        ),
    )

    body = load_linked_context(tasks_root=tasks_dir(project_dir), ticket_id="HATS-902")
    assert "EPIC DESCRIPTION BODY" in body
    assert "EPIC PLAN BODY" in body
    assert "RELEASE BODY" in body
    assert "LATEST ENTRY" in body and "OLD ENTRY" not in body
    assert body.index("HATS-900") < body.index("HATS-901")


def test_load_linked_context_module_empty_when_no_links(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(project_dir, TaskCard(id="HATS-902", title="lonely", state=TaskState.EXECUTE))
    root = tasks_dir(project_dir)
    assert load_linked_context(tasks_root=root, ticket_id="HATS-902") == ""
    assert load_linked_context(tasks_root=root, ticket_id="HATS-404") == ""
    assert load_linked_context(tasks_root=root, ticket_id="") == ""


def test_load_ticket_module(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(project_dir, TaskCard(id="HATS-902", title="t", state=TaskState.EXECUTE))
    root = tasks_dir(project_dir)
    assert "HATS-902" in load_ticket(tasks_root=root, ticket_id="HATS-902")
    assert load_ticket(tasks_root=root, ticket_id="HATS-404") == ""
