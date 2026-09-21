"""Session-scoped runtime-hook planning for the Codex surface.

Codex only discovers hooks from config layers.  The provider therefore passes
one stable dispatcher definition through ``-c`` while the composed hook list
stays in the ai-hats session cache.  Keeping session paths out of the command
is what makes Codex's reviewed hook hash reusable by concurrent sessions.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import (
    ENV_AI_HATS_PYTHON,
    ENV_SESSION_CACHE_DIR,
)
from .profile import PROFILE
from ai_hats.materialization import MaterializationEntry, describe_write_text

from ..hook_channel import surface_timeout
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .hook_dispatcher import DISPATCHER_COMMAND

#: Every bindable event, plus the arrival only this surface has. Derived, so a
#: new bindable event reaches Codex without anyone remembering this line.
CODEX_HOOK_EVENTS: tuple[str, ...] = PROFILE.native_events
MANIFEST_VERSION = 1


def _toml_string(value: str) -> str:
    """A JSON string is also a TOML basic string for this ASCII command."""
    return json.dumps(value, ensure_ascii=False)


def build_hook_cli_args() -> list[str]:
    """Return stable per-run Codex config overrides for the dispatcher.

    The definition contains no session/cache/project path and never opts out of
    hook trust.  Codex can therefore ask the user to review this exact command
    once instead of presenting a fresh hash for every ai-hats session.
    """
    # Omitting matcher is Codex's documented match-all form.  A literal `*`
    # looks like a glob but the matcher is regex-like and is not a valid
    # match-all expression on every CLI version.
    # Derived, never written by hand: bounding the dispatcher and the hook at
    # the same number is what made every timeout branch below unreachable and
    # left a killed chain with no verdict at all.
    handler = (
        '[{ hooks = [{ type = "command", command = '
        f"{_toml_string(DISPATCHER_COMMAND)}, timeout = {surface_timeout():.0f} }}] }}]"
    )
    args: list[str] = []
    for event in CODEX_HOOK_EVENTS:
        args.extend(["-c", f"hooks.{event}={handler}"])
    return args


def plan_hooks(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_root: Path,
) -> tuple[MaterializationEntry, dict[str, str]]:
    """The manifest entry and the pins, from the plan's runtime rows; what
    makes codex read it is ``build_hook_cli_args``, the same for every session."""
    manifest = describe_write_text(
        root / "hooks.json",
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "session": {
                    "id": root.name,
                    "ai_hats_dir": str(layout.base),
                    "skills_root": str(skills_root),
                },
                "hooks": manifest_rows(composition, skills_root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    env = {ENV_SESSION_CACHE_DIR: str(root), ENV_AI_HATS_PYTHON: str(host.python)}
    return manifest, env


__all__ = [
    "CODEX_HOOK_EVENTS",
    "MANIFEST_VERSION",
    "build_hook_cli_args",
    "plan_hooks",
]
