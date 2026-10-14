"""What kimi is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="kimi",
    # Kimi's built-in tools already carry the matcher vocabulary's names.
    tool_names={
        "Bash": ("Bash",),
        "Write": ("Write",),
        "Edit": ("Edit",),
        "Read": ("Read",),
        "Grep": ("Grep",),
        "Glob": ("Glob",),
    },
    native_events=BINDABLE_EVENTS,
    arg_names={},
    manifest_subpath=(),
    skills_subpath=("skills",),
    # PostToolUse stdout never reaches the model under kimi — nudges are
    # fire-and-forget, so the dialect refuses to carry them.
    # TODO(HATS-2061): a real nudge channel.
    speaks=Dialect(
        can_ask=False, can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=False
    ),
)

__all__ = ["PROFILE"]
