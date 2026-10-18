"""Session-scoped home delivery for the grok surface.

Grok reads global hooks from ``$GROK_HOME/hooks/*.json`` and user rules from
``$GROK_HOME/rules/*.md``. Both are the person's own files, so a per-session
chain and role need a per-session home. ``sessions/`` and ``logs/`` write
through into the real home: the parent resolver does not inherit the child's
``GROK_HOME``, and the transcript has to outlive the session-cache sweep.
Shims stay beside the manifest, not in ``hooks/`` — grok loads every ``*.json``
there, and a script in that directory is not a hook file.
"""

from __future__ import annotations

import dataclasses
import json
from importlib.resources import files
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from ai_hats.materialization import (
    MaterializationEntry,
    describe_mkdir,
    describe_symlink,
    describe_write_executable,
    describe_write_text,
    render_json,
)

from ..hook_channel import BINDABLE_EVENTS, surface_timeout
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .home import GROK_HOME_ENV, grok_home

MANIFEST_VERSION = 1
SESSION_HOME_SUBPATH = "grok-home"
HOOK_SHIMS: tuple[str, ...] = BINDABLE_EVENTS


def shim_source(name: str) -> str:
    """The packaged shim's bytes — the planner's own code, read like a constant."""
    return files("ai_hats.surfaces.grok").joinpath("hooks", name).read_text(encoding="utf-8")


def _grok_hooks(hooks_dir: Path, events: tuple[str, ...]) -> str:
    timeout = surface_timeout()
    document = {
        "hooks": {
            event: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hooks_dir / event),
                            "timeout": timeout,
                        }
                    ]
                }
            ]
            for event in events
        }
    }
    return render_json(document)


def plan_session_home(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_dir: Path,
    hooks_enabled: bool,
) -> tuple[list[MaterializationEntry], dict[str, str], Path]:
    """The per-session grok home. The role file is the planner's context write.

    Hooks join when the composition carries runtime rows and the session policy
    enables them. Returns entries, env, home.
    """
    rows = manifest_rows(composition, skills_dir) if hooks_enabled else {}
    hooks = {event: rows[event] for event in BINDABLE_EVENTS if event in rows}
    session_home = root / SESSION_HOME_SUBPATH
    hooks_dir = root / "hooks"
    global_home = grok_home()

    entries: list[MaterializationEntry] = [
        describe_mkdir(session_home),
        describe_mkdir(session_home / "rules"),
        describe_symlink(skills_dir, session_home / "skills"),
    ]
    auth = global_home / "auth.json"
    if auth.is_file():
        entries.append(describe_symlink(auth, session_home / "auth.json"))
    trust = global_home / "trusted_folders.toml"
    if trust.is_file():
        entries.append(describe_symlink(trust, session_home / "trusted_folders.toml"))
    for sub in ("sessions", "logs"):
        global_sub = global_home / sub
        entries.append(dataclasses.replace(describe_mkdir(global_sub), escape=True))
        entries.append(describe_symlink(global_sub, session_home / sub))
    if hooks:
        entries += [
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
        entries.append(describe_mkdir(session_home / "hooks"))
        entries.append(
            describe_write_text(
                session_home / "hooks" / "ai-hats.json",
                _grok_hooks(hooks_dir, HOOK_SHIMS),
            )
        )
    env = {
        GROK_HOME_ENV: str(session_home),
        ENV_SESSION_CACHE_DIR: str(root),
        ENV_AI_HATS_PYTHON: str(host.python),
    }
    return entries, env, session_home


__all__ = [
    "HOOK_SHIMS",
    "MANIFEST_VERSION",
    "SESSION_HOME_SUBPATH",
    "plan_session_home",
    "shim_source",
]
