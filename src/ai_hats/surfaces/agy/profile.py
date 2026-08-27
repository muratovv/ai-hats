"""What agy is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, FILE_MUTATION_NAMES, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="agy",
    tool_names={
        "run_command": ("Bash",),
        "execute": ("Bash",),
        "Create": FILE_MUTATION_NAMES,
        "write_to_file": FILE_MUTATION_NAMES,
        "replace_file_content": FILE_MUTATION_NAMES,
        "multi_replace_file_content": FILE_MUTATION_NAMES,
    },
    # agy registers three more in its global hook; nothing composed can bind
    # to them, and saying so here is what makes that visible.
    native_events=(*BINDABLE_EVENTS, "Stop", "Notification", "PostInvocation"),
    arg_names={
        "CommandLine": "command",
        "TargetFile": "file_path",
        "AbsolutePath": "file_path",
        "target_file": "file_path",
    },
    manifest_subpath=(),
    skills_subpath=("rules", ".agents", "skills"),
    speaks=Dialect(
        can_ask=True, can_ask_with_ticket=True, can_deny_after=True, can_carry_nudges=True
    ),
)

__all__ = ["PROFILE"]
