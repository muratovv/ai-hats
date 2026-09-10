"""Path conventions that are not geometry: the user home, the config refusal, legacy map.

The geometry itself — where ``sessions/``, ``tracker/``, ``library/``, the cache
and the versioned venvs live — is :class:`ai_hats_core.layout.ProjectLayout`,
built once at the composition root and handed down (ADR-0026). Nothing here
derives a path from a project directory any more; what stays is what is not a
function of the layout:

  - :func:`user_home` — the ai-hats-managed slice of the user's home.
  - :class:`ProjectConfigError` — the one refusal type for an unusable yaml.
  - :data:`LEGACY_PATH_MAP` / :func:`legacy_paths_by_class` — the pre-layout
    tree, consumed by the one-shot migrations inside ``Assembler.bump``.
  - :func:`editable_install_root` — locating this checkout from its dist.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .. import env
from .constants import (
    AI_HATS_PROJECT_DIR_ENV as AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR as ENV_AI_HATS_DIR,
    ENV_AI_HATS_VENV as ENV_AI_HATS_VENV,
)

if TYPE_CHECKING:
    from ai_hats_core.layout import ProjectLayout


LegacyClass = Literal["sessions", "tracker", "library", "root"]


class ProjectConfigError(ValueError):
    """ai-hats.yaml cannot be honoured — one family for EVERY reader.

    Lives in the paths leaf so ``config.project`` and the shell-facing readers
    refuse with one type; a config rejected by one reader can no longer be
    obeyed by another.
    """


def user_home() -> Path:
    """User home for ai-hats-managed global artefacts (HATS-532).

    Precedence:
      1. ``AI_HATS_USER_HOME`` env var — runtime override, ``~`` expanded.
      2. Default ``Path.home()``.

    Why a dedicated knob (vs. just letting tests set ``HOME``): on
    macOS, claude-cli auth lives in the Keychain entry
    ``Claude Code-credentials``, scoped per the real ``HOME``. Setting
    ``HOME=<tmp>`` for an e2e test cascades into the spawned claude
    binary and produces ``Not logged in``. ``AI_HATS_USER_HOME``
    intercepts ONLY the ai-hats-managed `~/.ai-hats/` resolution,
    leaving ``HOME`` (and therefore claude auth) intact.

    Sanctioned call sites — and these are the ONLY places that should
    bypass ``Path.home()`` for the global ai-hats slice:
      - :meth:`UserConfig.default_path`
      - :class:`Assembler` global library layer
      - ``cli.maintenance._snapshot_library``
      - :func:`ai_hats_core.layout.cache_home`, which mirrors this chain in core

    Other ``Path.home()`` usages in the codebase (e.g. ``~/.claude/``
    skills marker, expanding user-supplied ``~`` in CLI paths) are
    NOT covered by this override — they're not ai-hats-managed global
    state.
    """
    raw = env.user_home_override()
    return Path(raw).expanduser() if raw else Path.home()


# ---------- Legacy migration helpers ----------

# Maps a legacy path (relative to the project root) to (class, new path
# relative to the layout base); each migration pulls only its class.

LEGACY_PATH_MAP: dict[str, tuple[LegacyClass, str]] = {
    # Sessions — `.gitlog/` holds both pipeline_runs/ and session_<id>/
    # subdirs; the whole tree moves to sessions/runs/ in one shot.
    ".gitlog": ("sessions", "sessions/runs"),
    ".agent/retrospectives": ("sessions", "sessions/retros"),
    ".agent/audits": ("sessions", "sessions/audits"),
    ".agent/handoffs": ("sessions", "sessions/handoffs"),
    ".agent/experiments": ("sessions", "sessions/experiments"),
    ".agent/worktrees": ("sessions", "sessions/worktrees"),
    ".agent/worktree.json": ("sessions", "sessions/worktree.json"),
    # Tracker
    ".agent/backlog": ("tracker", "tracker/backlog"),
    ".agent/hypotheses": ("tracker", "tracker/hypotheses"),
    ".agent/decisions": ("tracker", "tracker/decisions"),
    ".agent/STATE.md": ("tracker", "STATE.md"),
    # Library
    ".agent/rules": ("library", "library/rules"),
    ".agent/skills": ("library", "library/skills"),
    ".agent/hooks": ("library", "library/hooks"),
    # Framework root
    ".agent/.last_backup": ("root", ".last_backup"),
}


def legacy_paths_by_class(
    layout: ProjectLayout,
    class_: LegacyClass,
) -> list[tuple[Path, Path]]:
    """Return ``[(old_abs, new_abs)]`` for legacy paths of one class only.

    Drives the one-shot migrations inside ``Assembler.bump``: each caller
    pulls only its class. Does NOT perform any move.
    """
    base = layout.base
    out: list[tuple[Path, Path]] = []
    for legacy, (c, new_rel) in LEGACY_PATH_MAP.items():
        if c != class_:
            continue
        old_abs = layout.root / legacy
        if old_abs.exists():
            out.append((old_abs, base / new_rel))
    return out


def editable_install_root(dist_name: str = "ai-hats") -> Path | None:
    """Filesystem root of an editable (PEP 660) install of ``dist_name``, else None.

    Reads the dist's PEP 610 ``direct_url.json`` and returns the ``file://`` path
    when ``dir_info.editable`` is set — a reusable way for any consumer to locate
    its own editable checkout (e.g. surface-plugin self-heal, HATS-966). Read-only;
    tolerant of missing / malformed metadata (returns None).
    """
    import json
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        raw = distribution(dist_name).read_text("direct_url.json")
    except (PackageNotFoundError, FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not (data.get("dir_info") or {}).get("editable"):
        return None
    prefix = "file://"
    url = data.get("url") or ""
    return Path(url[len(prefix) :]) if url.startswith(prefix) else None


__all__ = [
    "LegacyClass",
    "editable_install_root",
    "AI_HATS_PROJECT_DIR_ENV",
    "ENV_AI_HATS_DIR",
    "ENV_AI_HATS_VENV",
    "ProjectConfigError",
    "user_home",
    "LEGACY_PATH_MAP",
    "legacy_paths_by_class",
]
