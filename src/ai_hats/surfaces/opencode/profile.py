"""What opencode is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, FILE_MUTATION_NAMES, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="opencode",
    tool_names={
        "bash": ("Bash",),
        "read": ("Read",),
        "edit": ("Edit",),
        "write": ("Write",),
        "patch": FILE_MUTATION_NAMES,
        "glob": ("Glob",),
        "list": ("Glob",),
        "grep": ("Grep",),
        "task": ("Task",),
        "webfetch": ("WebFetch",),
        "websearch": ("WebFetch",),
        "todowrite": ("TodoWrite",),
    },
    native_events=BINDABLE_EVENTS,
    # Observed, not guessed: opencode's own session database records `filePath`
    # on every read, edit and write, while `grep` and `glob` send `path`, which
    # the shipped gates already read. A gate handed neither ALLOWS the call
    # (`wt_gate.py`), so an empty row here matched the tool and inspected
    # nothing.
    arg_names={"filePath": "file_path"},
    manifest_subpath=("opencode",),
    skills_subpath=("opencode-xdg", "opencode", "skills"),
    speaks=Dialect(
        can_ask=False,
        can_ask_with_ticket=False,
        can_deny_after=False,
        # No channel into the model's context; the plugin puts a nudge on the
        # operator's console, which is onward and not nowhere.
        can_carry_nudges=True,
    ),
)

__all__ = ["PROFILE"]
