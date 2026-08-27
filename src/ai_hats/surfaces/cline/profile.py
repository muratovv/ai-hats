"""What cline is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="cline",
    tool_names={
        "bash": ("Bash",),
        "execute_command": ("Bash",),
        "run_commands": ("Bash",),
        "write_to_file": ("Write",),
        "replace_in_file": ("Edit",),
        "editor": ("Edit",),
        "apply_patch": ("Edit",),
        "read_file": ("Read",),
        "read_files": ("Read",),
        "search_files": ("Grep",),
        "search_codebase": ("Grep",),
        "search": ("Grep",),
        "list_files": ("Glob",),
    },
    arg_names={},
    manifest_subpath=(),
    skills_subpath=("skills",),
    speaks=Dialect(can_ask_with_ticket=False, can_deny_after=False, can_carry_nudges=True),
)

__all__ = ["PROFILE"]
