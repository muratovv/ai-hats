"""What codex is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, FILE_MUTATION_NAMES, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="codex",
    tool_names={
        # `exec` is what codex-cli sends; the other two are the spellings older
        # and internal builds use for the same tool, and a missed spelling
        # silently disarms every terminal gate rather than failing anything.
        "exec": ("Bash",),
        "shell": ("Bash",),
        "local_shell": ("Bash",),
        "apply_patch": FILE_MUTATION_NAMES,
        "spawn_agent": ("Agent",),
    },
    # PermissionRequest is Codex's own arrival for a call the chain judges as
    # a PreToolUse; the dispatcher maps it before anything else sees it.
    native_events=(*BINDABLE_EVENTS, "PermissionRequest"),
    arg_names={},
    manifest_subpath=(),
    # Codex mirrors skills outside the session cache, so only the manifest it
    # wrote knows where they landed.
    skills_subpath=None,
    speaks=Dialect(
        can_ask=True, can_ask_with_ticket=False, can_deny_after=True, can_carry_nudges=True
    ),
)

__all__ = ["PROFILE"]
