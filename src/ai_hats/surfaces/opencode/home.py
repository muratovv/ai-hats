"""The person's OpenCode config home as planning sees it (ADR-0036 D2):
enumerated once before the plan by ``probe_home`` and handed in as
``Host.home``, then projected into the session's XDG root by
``plan_projection`` — the planner reads nothing itself."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_hats.materialization import MaterializationEntry, describe_symlink

from ..plan import Digested

ENV_OPENCODE_CONFIG = "OPENCODE_CONFIG"
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
#: Where the user's real opencode config home lives. Points at the BASE
#: (``~/.config``), not at the ``opencode/`` dir inside it — same shape as
#: ``XDG_CONFIG_HOME`` itself.
ENV_OPENCODE_CONFIG_HOME = "AI_HATS_OPENCODE_CONFIG_HOME"


@dataclass(frozen=True)
class OpenCodeHome(Digested):
    """The facts of the person's config home a session projects."""

    #: ``<base>/opencode``, as configured — the links point at it as spelled.
    config_dir: Path
    #: The same directory resolved; ``None`` where there is no such directory,
    #: so nothing is projected.
    resolved: Path | None
    #: Names in the config dir the session links, in name order; ``skills`` is never one.
    entries: tuple[str, ...]
    #: Names under ``<config_dir>/skills``, in name order; empty without that directory.
    skills: tuple[str, ...]


def base_config_home(environ: Mapping[str, str]) -> Path:
    """The user's real config home (the base, not ``opencode/``)."""
    configured = environ.get(ENV_OPENCODE_CONFIG_HOME) or environ.get(ENV_XDG_CONFIG_HOME)
    candidate = Path(configured).expanduser() if configured else Path.home() / ".config"
    if not candidate.is_absolute():
        raise RuntimeError("OpenCode config home must be an absolute directory")
    return candidate


def probe_home(environ: Mapping[str, str]) -> OpenCodeHome:
    """The one read of the home a plan is built on: what the session links
    beside its own config, and the person's own skills."""
    config_dir = base_config_home(environ) / "opencode"
    if not config_dir.is_dir():
        return OpenCodeHome(config_dir=config_dir, resolved=None, entries=(), skills=())
    try:
        entries = tuple(sorted(path.name for path in config_dir.iterdir() if path.name != "skills"))
    except OSError:
        raise RuntimeError("OpenCode base config projection failed") from None
    base_skills = config_dir / "skills"
    skills: tuple[str, ...] = ()
    if base_skills.is_dir():
        try:
            skills = tuple(sorted(path.name for path in base_skills.iterdir()))
        except OSError:
            raise RuntimeError("OpenCode base skill projection failed") from None
    return OpenCodeHome(
        config_dir=config_dir, resolved=config_dir.resolve(), entries=entries, skills=skills
    )


def plan_projection(
    home: OpenCodeHome, session_config_dir: Path, *, mirrored: set[str]
) -> list[MaterializationEntry]:
    """User-owned config entries linked next to the session-owned ones, and
    the person's own skills the composition does not shadow; nothing where
    the person has no config dir."""
    if home.resolved is None:
        return []
    # Lexical, not resolved: the plan reads no disk, and the session root is
    # the planner's own input.
    session = Path(os.path.normpath(session_config_dir))
    if (
        home.resolved == session
        or home.resolved in session.parents
        or session in home.resolved.parents
    ):
        raise RuntimeError("OpenCode base config home must be outside the session XDG root")
    skills_root = session_config_dir / "skills"
    return [
        *(
            describe_symlink(home.config_dir / name, session_config_dir / name)
            for name in home.entries
        ),
        *(
            describe_symlink(home.config_dir / "skills" / name, skills_root / name)
            for name in home.skills
            if name not in mirrored
        ),
    ]


__all__ = [
    "ENV_OPENCODE_CONFIG",
    "ENV_OPENCODE_CONFIG_HOME",
    "ENV_XDG_CONFIG_HOME",
    "OpenCodeHome",
    "base_config_home",
    "plan_projection",
    "probe_home",
]
