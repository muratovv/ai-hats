"""Unit tests for ``ai_hats.surfaces.claude.sdk_options``: the first user
message and how a record names an option document."""

from __future__ import annotations

import pytest

from ai_hats.surfaces.claude.sdk_options import build_first_user_message, describe_options


# ---------------------------------------------------------------------------
# describe_options
# ---------------------------------------------------------------------------


def test_describe_options_names_what_ai_hats_set_and_hides_env_values() -> None:
    from claude_agent_sdk import ClaudeAgentOptions

    described = describe_options(
        ClaudeAgentOptions(cwd="/wt", model="m", env={"B": "secret", "A": "secret"})
    )

    assert described == ["env=['A', 'B']", "model=m"], "cwd omitted, env by key name only"


# ---------------------------------------------------------------------------
# build_first_user_message
# ---------------------------------------------------------------------------


def test_build_first_user_message_all_empty_returns_empty() -> None:
    assert build_first_user_message() == ""


def test_build_first_user_message_task_only() -> None:
    msg = build_first_user_message(task="Do thing")
    assert msg == "# TASK\nDo thing"


def test_build_first_user_message_linked_only() -> None:
    msg = build_first_user_message(linked_context="parent card")
    assert msg == "# LINKED_CONTEXT\nparent card"


def test_build_first_user_message_ticket_only() -> None:
    msg = build_first_user_message(ticket_context="ticket body")
    assert msg == "# TICKET_CONTEXT\nticket body"


def test_build_first_user_message_section_order() -> None:
    """When all sections are present, order is TICKET → LINKED → TASK."""
    msg = build_first_user_message(
        ticket_context="ticket",
        linked_context="linked",
        task="task",
    )
    ticket_idx = msg.index("# TICKET_CONTEXT")
    linked_idx = msg.index("# LINKED_CONTEXT")
    task_idx = msg.index("# TASK")
    assert ticket_idx < linked_idx < task_idx


def test_build_first_user_message_skips_empty_sections() -> None:
    """Empty sections are omitted entirely — no blank section headers."""
    msg = build_first_user_message(linked_context="", ticket_context="t", task="T")
    assert "LINKED_CONTEXT" not in msg
    assert "# TICKET_CONTEXT\nt" in msg
    assert "# TASK\nT" in msg


def test_build_first_user_message_has_no_project_state_channel() -> None:
    """HATS-681 dropped the section; the parameter outlived it, promising a
    channel no caller ever fed."""
    with pytest.raises(TypeError):
        build_first_user_message(project_state="cwd=...")  # type: ignore[call-arg]


def test_build_first_user_message_sections_separated_by_blank_line() -> None:
    msg = build_first_user_message(ticket_context="t", task="T")
    assert msg == "# TICKET_CONTEXT\nt\n\n# TASK\nT"
