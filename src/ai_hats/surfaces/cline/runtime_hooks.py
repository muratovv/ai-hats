"""Session-scoped runtime-hook delivery for the Cline surface."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from .profile import PROFILE
from ai_hats.materialization import (
    MaterializationEntry,
    describe_write_executable,
    describe_write_text,
)

from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host

CLINE_HOOK_EVENTS: tuple[str, ...] = PROFILE.native_events
MANIFEST_VERSION = 1
#: The shims cline spawns from ``--hooks-dir``, one per arrival it has; each
#: hands the call to the dispatcher for the event it is named after.
HOOK_SHIMS: tuple[str, ...] = ("PreToolUse", "PostToolUse")


def shim_source(name: str) -> str:
    """The packaged shim's bytes — the planner's own code, read like a constant."""
    return files("ai_hats.surfaces.cline").joinpath("hooks", name).read_text(encoding="utf-8")


def plan_hooks(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_dir: Path,
) -> tuple[list[MaterializationEntry], dict[str, str]] | None:
    """The shims, the manifest and the pins from the plan's runtime rows;
    ``None`` for a composition with no chain to run — cline then gets no
    ``--hooks-dir`` at all, as the builder gave it none."""
    rows = manifest_rows(composition, skills_dir)
    hooks = {event: rows[event] for event in CLINE_HOOK_EVENTS if event in rows}
    if not hooks:
        return None
    hooks_dir = root / "hooks"
    entries = [
        describe_write_executable(hooks_dir / name, shim_source(name)) for name in HOOK_SHIMS
    ]
    entries.append(
        describe_write_text(
            root / "hooks.json",
            json.dumps(
                {
                    "version": MANIFEST_VERSION,
                    "session": {"id": root.name, "ai_hats_dir": str(layout.base)},
                    "hooks": hooks,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    )
    env = {ENV_SESSION_CACHE_DIR: str(root), ENV_AI_HATS_PYTHON: str(host.python)}
    return entries, env


__all__ = [
    "CLINE_HOOK_EVENTS",
    "HOOK_SHIMS",
    "MANIFEST_VERSION",
    "plan_hooks",
    "shim_source",
]
