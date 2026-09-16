"""What the SDK launch of a sub-agent says about itself: the option document
as a record names it, and the audit text beside the system prompt.

The option document itself is built by ``ClaudeSurface.plan`` and
``automate_launch`` (ADR-0036 D2, D4); the first user turn is the launch's
``brief`` (``session_artifacts.assemble_brief``). Nothing here materializes
anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions


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


def render_sdk_audit(system_text: str, first_message: str) -> str:
    return (
        "==== SDK system_prompt (preset=claude_code, append) ====\n"
        f"{system_text}\n"
        "\n"
        "==== SDK first user message ====\n"
        f"{first_message}\n"
    )
