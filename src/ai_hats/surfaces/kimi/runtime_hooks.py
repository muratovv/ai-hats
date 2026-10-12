"""Session-scoped runtime-hook delivery for the Kimi surface.

Kimi reads hooks only from ``$KIMI_CODE_HOME/config.toml`` — a user-global
file, so a per-session hook chain needs a per-session home (the codex
session-home pattern): the session's home carries a snapshot of the global
config with the composed ``[[hooks]]`` appended, and symlinks ``credentials/``
back so OAuth keeps working. Sessions, logs and the wire transcript then land
under the session cache too — ``resolve_transcript`` follows the same env.
"""

from __future__ import annotations

import json
import logging
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
)

from ..hook_channel import BINDABLE_EVENTS
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .home import kimi_home

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
SESSION_HOME_SUBPATH = "kimi-home"
HOOK_SHIMS: tuple[str, ...] = BINDABLE_EVENTS


def shim_source(name: str) -> str:
    """The packaged shim's bytes — the planner's own code, read like a constant."""
    return files("ai_hats.surfaces.kimi").joinpath("hooks", name).read_text(encoding="utf-8")


def _toml_literal(value: str) -> str:
    if "'" in value:
        raise ValueError(f"hook command cannot carry a single quote in TOML literal: {value!r}")
    return f"'{value}'"


def _config_with_hooks(base: str, hooks_dir: Path, events: tuple[str, ...]) -> str:
    blocks = []
    for event in events:
        command = str(hooks_dir / event)
        blocks.append(
            f'[[hooks]]\nevent = "{event}"\ncommand = {_toml_literal(command)}\ntimeout = 30\n'
        )
    return base.rstrip() + "\n\n" + "\n".join(blocks)


def plan_hooks(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_dir: Path,
) -> tuple[list[MaterializationEntry], dict[str, str]] | None:
    """The per-session kimi home with the composed hook chain; ``None`` for a
    composition with no chain to run — kimi then keeps its own global config."""
    rows = manifest_rows(composition, skills_dir)
    hooks = {event: rows[event] for event in BINDABLE_EVENTS if event in rows}
    if not hooks:
        return None
    if "PostToolUse" in hooks:
        logger.warning(
            "kimi: PostToolUse is observation-only (stdout never reaches the "
            "model) — composed nudge hooks will not nudge"
        )
    session_home = root / SESSION_HOME_SUBPATH
    hooks_dir = root / "hooks"
    global_home = kimi_home()
    base_config = ""
    global_config = global_home / "config.toml"
    if global_config.is_file():
        base_config = global_config.read_text(encoding="utf-8")
    else:
        logger.warning(
            "kimi: no global config.toml at %s — session home starts empty", global_config
        )

    entries: list[MaterializationEntry] = [describe_mkdir(session_home)]
    credentials = global_home / "credentials"
    if credentials.is_dir():
        entries.append(describe_symlink(credentials, session_home / "credentials"))
    entries += [
        describe_write_executable(hooks_dir / name, shim_source(name)) for name in HOOK_SHIMS
    ]
    entries.append(
        describe_write_text(
            session_home / "config.toml",
            _config_with_hooks(base_config, hooks_dir, HOOK_SHIMS),
        )
    )
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
    env = {
        "KIMI_CODE_HOME": str(session_home),
        ENV_SESSION_CACHE_DIR: str(root),
        ENV_AI_HATS_PYTHON: str(host.python),
    }
    return entries, env


__all__ = ["HOOK_SHIMS", "MANIFEST_VERSION", "plan_hooks", "shim_source"]
