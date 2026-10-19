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
    # Measured on the hooks guide: PreToolUse accepts deny, ask, and updatedInput.
    # PostToolUse can nudge and cannot un-run a call. A ticketed ask is one reply.
    speaks=Dialect(
        can_ask=True,
        can_ask_with_ticket=True,
        can_deny_after=False,
        can_carry_nudges=True,
    ),
)

__all__ = ["PROFILE"]
