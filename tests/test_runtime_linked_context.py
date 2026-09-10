"""HATS-689 (Req 1 of HATS-688): linked-task context injection.

``linked_context.load_linked_context`` assembles a ``LINKED_CONTEXT`` body from
a ticket's *direct* links (parent epic first, then depends_on / related /
see_also), trimmed per-card with only the latest work_log entry; the parent epic
additionally carries its ``plan.md``. The body is wired into both live sub-agent
prompt channels (``build_first_user_message`` for Claude, ``assemble_meta_prompt``
for Agy).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

from ai_hats.linked_context import load_linked_context
from ai_hats.session_artifacts import assemble_meta_prompt
from ai_hats_rack.models import TaskCard


def _linked(project_dir: Path, ticket_id: str) -> str:
    return load_linked_context(
        tasks_root=ProjectLayout.at(project_dir).tracker.tasks_dir, ticket_id=ticket_id
    )


def _write_card(project_dir: Path, card: TaskCard, plan_body: str | None = None) -> None:
    card_dir = ProjectLayout.at(project_dir).tracker.tasks_dir / card.id
    # The rack's atomic write needs the card dir to exist (the tracker's went
    # through ai_hats_core.atomic_write_text, which created parents itself).
    card_dir.mkdir(parents=True, exist_ok=True)
    card.save(card_dir / "task.yaml")
    if plan_body is not None:
        (card_dir / "plan.md").write_text(plan_body)


def test_load_linked_context_happy_path_and_ordering(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    _write_card(
        project_dir,
        TaskCard(
            id="HATS-900",
            title="Epic: release train",
            state="execute",
            description="EPIC DESCRIPTION BODY",
            work_log=[
                {"timestamp": "2026-01-01T00:00:00Z", "message": "OLD EPIC ENTRY"},
                {"timestamp": "2026-02-02T00:00:00Z", "message": "LATEST EPIC ENTRY"},
            ],
        ),
        plan_body="# EPIC PLAN\nEPIC PLAN BODY",
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-901",
            title="Release 1.2.0",
            state="done",
            description="RELEASE DESCRIPTION BODY",
        ),
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-902",
            title="Bug in the release",
            state="execute",
            description="the child ticket",
            parent_task="HATS-900",
            related=["HATS-901"],
        ),
    )

    body = _linked(project_dir, "HATS-902")

    # Parent epic: trimmed card + plan.md.
    assert "HATS-900" in body
    assert "EPIC DESCRIPTION BODY" in body
    assert "EPIC PLAN BODY" in body
    # Related (release): card only.
    assert "HATS-901" in body
    assert "RELEASE DESCRIPTION BODY" in body
    # work_log trimmed to the latest entry only.
    assert "LATEST EPIC ENTRY" in body
    assert "OLD EPIC ENTRY" not in body
    # Salience order: parent epic precedes the related link.
    assert body.index("HATS-900") < body.index("HATS-901")


def test_load_linked_context_no_links_returns_empty(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(
        project_dir,
        TaskCard(id="HATS-902", title="lonely ticket", state="execute"),
    )
    assert _linked(project_dir, "HATS-902") == ""


def test_load_linked_context_missing_target_is_skipped(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-900",
            title="Real epic",
            state="execute",
            description="REAL EPIC BODY",
        ),
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-902",
            title="child",
            state="execute",
            parent_task="HATS-900",
            related=["HATS-404"],  # dangling: no such card
        ),
    )
    body = _linked(project_dir, "HATS-902")
    assert "REAL EPIC BODY" in body
    assert "HATS-404" not in body  # graceful skip, no crash


def test_load_linked_context_epic_without_plan_is_card_only(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-900",
            title="Epic no plan",
            state="execute",
            description="EPIC CARD ONLY",
        ),
        # no plan_body → no plan.md written
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-902",
            title="child",
            state="execute",
            parent_task="HATS-900",
        ),
    )
    body = _linked(project_dir, "HATS-902")
    assert "EPIC CARD ONLY" in body
    assert "plan.md" not in body


def test_load_linked_context_unknown_ticket_returns_empty(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    assert _linked(project_dir, "HATS-999") == ""
    assert _linked(project_dir, "") == ""


# --- wiring into the two live prompt channels ---


def test_build_first_user_message_wires_linked_context_after_ticket() -> None:
    """Claude live channel: LINKED_CONTEXT sits after TICKET_CONTEXT."""
    from ai_hats.surfaces.claude.sdk_options import build_first_user_message

    msg = build_first_user_message(
        ticket_context="TICKET BODY",
        linked_context="LINKED BODY",
        task="do the thing",
    )
    assert "# LINKED_CONTEXT\nLINKED BODY" in msg
    assert msg.index("# TICKET_CONTEXT") < msg.index("# LINKED_CONTEXT") < msg.index("# TASK")

    # Empty linked_context emits no section.
    msg_empty = build_first_user_message(ticket_context="T", task="t")
    assert "# LINKED_CONTEXT" not in msg_empty


def test_assemble_meta_prompt_wires_linked_context_section(tmp_path: Path) -> None:
    """Agy live channel: assemble_meta_prompt emits LINKED_CONTEXT after TICKET_CONTEXT."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-900",
            title="Epic",
            state="execute",
            description="EPIC BODY FOR AGY",
        ),
    )
    _write_card(
        project_dir,
        TaskCard(
            id="HATS-902",
            title="child",
            state="execute",
            parent_task="HATS-900",
        ),
    )
    out = assemble_meta_prompt(
        ProjectLayout.at(project_dir),
        role_context="# SYSTEM_ROLE\nstub",
        task="go",
        ticket_id="HATS-902",
    )
    assert "# TICKET_CONTEXT" in out
    assert "# LINKED_CONTEXT" in out
    assert "EPIC BODY FOR AGY" in out
    assert out.index("# TICKET_CONTEXT") < out.index("# LINKED_CONTEXT")

    # A ticket with no links → no LINKED_CONTEXT section.
    _write_card(
        project_dir,
        TaskCard(id="HATS-903", title="lonely", state="execute"),
    )
    out_nolinks = assemble_meta_prompt(
        ProjectLayout.at(project_dir),
        role_context="# SYSTEM_ROLE\nstub",
        task="go",
        ticket_id="HATS-903",
    )
    assert "# LINKED_CONTEXT" not in out_nolinks
