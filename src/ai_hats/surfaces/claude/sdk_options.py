"""What the SDK launch of a sub-agent says about itself: the option document
as a record names it, and the first user turn beside the system prompt.

The option document itself is built by ``ClaudeSurface.plan`` and
``automate_launch`` (ADR-0036 D2, D4); nothing here materializes anything.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions


#: What a launch record shows for ``session_id``: the runner mints the real one
#: per run, so a record written to be compared against a dry run cannot carry it.
SESSION_ID_PLACEHOLDER = "<minted at launch>"


def describe_options(options: "ClaudeAgentOptions") -> list[str]:
    """``k=v`` for every option ai-hats set, measured against the SDK's defaults.

    ``env`` is rendered as key names — the report hides env values everywhere
    else, and naming them here would be the same leak by another route. ``cwd``
    is omitted: it is the worktree, which does not exist when the record is
    written, and the record's ``cwd`` carries that sentinel already.
    """  # comment-length: allow — both omissions are deliberate and easy to "fix" wrongly
    import dataclasses

    from claude_agent_sdk import ClaudeAgentOptions

    stock = ClaudeAgentOptions()
    described = []
    for field in dataclasses.fields(options):
        value = getattr(options, field.name)
        if field.name == "cwd" or value == getattr(stock, field.name):
            continue
        described.append(f"{field.name}={sorted(value) if field.name == 'env' else value}")
    return sorted(described)


def build_first_user_message(
    *,
    ticket_context: str = "",
    task: str = "",
    linked_context: str = "",
) -> str:
    """Compose the first user message for a sub-agent session.

    ``TICKET_CONTEXT``, ``LINKED_CONTEXT``, ``TASK`` — in that
    order. Per-task runtime context rides the first user turn; the per-role
    composition rides ``system_prompt``. Empty sections are skipped, all-empty
    returns ``""``. A fourth section, ``PROJECT_STATE``, was dropped, and its
    parameter removed later.

    ``LINKED_CONTEXT`` carries the cards of the ticket's directly-linked
    tasks, assembled by ``linked_context.load_linked_context``. This is the
    live Claude channel for that section; the CLI surfaces take the same
    sections as the ``brief`` of their launch (``session_artifacts.assemble_brief``).

    Callers reach this through :func:`assemble_first_user_message`, which is
    what loads the sections — going direct is how the engine ended up sending
    a one-line stand-in for the card.
    """
    sections: list[str] = []
    if ticket_context:
        sections.append(f"# TICKET_CONTEXT\n{ticket_context}")
    if linked_context:
        sections.append(f"# LINKED_CONTEXT\n{linked_context}")
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)


def assemble_first_user_message(layout: ProjectLayout, *, task: str, ticket_id: str) -> str:
    """The SDK's first user turn — one expression for the engine and the audit.

    The engine sent ``Ticket: <id>`` while the saved audit rendered
    the whole card plus ``LINKED_CONTEXT``, so ``meta_prompt.txt`` named a
    message the SDK had never received.
    """
    from ai_hats.session_artifacts import assemble_brief

    return assemble_brief(layout, task=task, ticket_id=ticket_id)


def render_sdk_audit(system_text: str, first_message: str) -> str:
    return (
        "==== SDK system_prompt (preset=claude_code, append) ====\n"
        f"{system_text}\n"
        "\n"
        "==== SDK first user message ====\n"
        f"{first_message}\n"
    )
