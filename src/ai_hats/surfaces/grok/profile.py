"""What grok is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="grok",
    # Native names are what ``updates.jsonl`` and the hook stdin ``tool_name``
    # carry (measured on grok 1.0.62). The tuple is the matcher vocabulary.
    tool_names={
        "run_terminal_command": ("Bash",),
        "write": ("Write",),
        "search_replace": ("Edit",),
        "read_file": ("Read",),
        "grep": ("Grep",),
        "list_dir": ("Glob",),
    },
    native_events=BINDABLE_EVENTS,
    arg_names={},
    manifest_subpath=(),
    skills_subpath=("skills",),
    # A client that approves prompts itself runs a ticketed ask in the same turn,
    # so that reply is refused. A question with no rewrite is still asked.
    speaks=Dialect(
        can_ask=True,
        can_ask_with_ticket=False,
        can_deny_after=False,
        can_carry_nudges=True,
    ),
)

__all__ = ["PROFILE"]
