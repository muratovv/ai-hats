"""Session-scoped home delivery for the Kimi surface.

Kimi reads hooks only from ``$KIMI_CODE_HOME/config.toml`` and global
instructions from ``$KIMI_CODE_HOME/AGENTS.md`` — both user-global files, so a
per-session chain and role need a per-session home (the codex session-home
pattern): the session's home carries a snapshot of the global config with the
composed ``[[hooks]]`` appended, copies ``tui.toml`` (status_line) over, and
symlinks ``credentials/`` and ``workspace-trust/`` back so OAuth and
folder-trust decisions survive the session. ``sessions/`` and ``logs/`` also
write through symlinks into the global home — the wire transcript must outlive
the session cache, which the runner sweeps at run close.
"""  # comment-length: allow — the per-session-home contract is the module's point

from __future__ import annotations

import dataclasses
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

from ..hook_channel import BINDABLE_EVENTS, surface_timeout
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .home import KIMI_HOME_ENV, kimi_home

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
SESSION_HOME_SUBPATH = "kimi-home"
HOOK_SHIMS: tuple[str, ...] = BINDABLE_EVENTS

#: kimi refuses a hook ``timeout`` outside 1..600, failing the WHOLE config
#: load — an oversized chain budget must clamp, not disarm every gate.
KIMI_HOOK_TIMEOUT_LIMIT_S = 600


def _hook_timeout() -> float:
    bound = surface_timeout()
    if bound > KIMI_HOOK_TIMEOUT_LIMIT_S:
        logger.warning(
            "kimi: hook timeout %.0fs exceeds kimi's %ds limit — clamped; "
            "the chain may be killed mid-flight and fail open",
            bound,
            KIMI_HOOK_TIMEOUT_LIMIT_S,
        )
        return float(KIMI_HOOK_TIMEOUT_LIMIT_S)
    return bound


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
        # The outer bound must outlive the chain's own budget — a hand-written
        # number smaller than it lets kimi kill the dispatcher mid-chain and
        # fail open with no verdict (the lesson surface_timeout() encodes).
        blocks.append(
            f'[[hooks]]\nevent = "{event}"\ncommand = {_toml_literal(command)}'
            f"\ntimeout = {_hook_timeout():.0f}\n"
        )
    return base.rstrip() + "\n\n" + "\n".join(blocks)


def plan_session_home(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_dir: Path,
    hooks_enabled: bool,
) -> tuple[list[MaterializationEntry], dict[str, str], Path]:
    """The per-session kimi home — always: the composed role lands in its
    ``AGENTS.md``, so the home exists with or without a hook chain. Hooks join
    when the composition carries runtime rows AND the session policy enables
    them (ADR-0018: session wiring is gated by ``SessionPolicy.hooks``).
    Returns entries, env, home."""
    rows = manifest_rows(composition, skills_dir) if hooks_enabled else {}
    hooks = {event: rows[event] for event in BINDABLE_EVENTS if event in rows}
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
    # sessions/ and logs/ write THROUGH symlinks into the global home: the wire
    # transcript must outlive the session cache (swept at run close), and
    # `kimi -r` keeps working off the same records.
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
        config = _config_with_hooks(base_config, hooks_dir, HOOK_SHIMS)
    else:
        config = base_config.rstrip() + "\n"
    entries.append(describe_write_text(session_home / "config.toml", config))
    # tui.toml carries [status_line].command — kimi recreates it with defaults
    # in a fresh home, so a session would lose the bar unless it rides along.
    global_tui = global_home / "tui.toml"
    if global_tui.is_file():
        entries.append(
            describe_write_text(session_home / "tui.toml", global_tui.read_text(encoding="utf-8"))
        )
    else:
        logger.warning("kimi: no global tui.toml at %s — session home goes without", global_tui)
    # workspace-trust/ holds kimi's "Trust this folder?" records — write-through
    # symlink into the global home, or every session re-asks and a decision taken
    # inside a session dies with the swept cache.
    global_trust = global_home / "workspace-trust"
    entries.append(dataclasses.replace(describe_mkdir(global_trust), escape=True))
    entries.append(describe_symlink(global_trust, session_home / "workspace-trust"))
    env = {
        KIMI_HOME_ENV: str(session_home),
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
