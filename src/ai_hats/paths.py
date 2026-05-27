"""Path conventions for ai-hats runtime + user config (HATS-316).

Single source of truth for "where does ai-hats keep its files?". All
framework-managed artefacts live under ``<ai_hats_dir>/`` — by default
``<project>/.agent/ai-hats/`` — split into three classes:

  - ``sessions/``  — per-run / runtime artefacts (runs, retros, audits,
    handoffs, experiments, worktrees).
  - ``tracker/``   — cards + records with lifecycle (backlog, hypotheses,
    decisions); plus ``STATE.md`` and ``.last_backup`` on the dir root.
  - ``library/``   — managed mirrors of the role composition (rules,
    skills, hooks) for external consumers.

Resolution of ``ai_hats_dir`` itself follows the precedence chain:

  1. ``AI_HATS_DIR`` env var — runtime override (tests, sandbox, debug).
  2. ``ai-hats.yaml`` ``ai_hats_dir`` field (relative to project root).
  3. Bootstrap fallback ``.agent/ai-hats/`` — used pre-migration, fresh
     projects, or tests without yaml. ``ProjectConfig`` itself treats the
     field as required and will raise ``ValidationError`` if it's missing
     from a v4 yaml; ``paths.py`` is the low-level resolver that needs to
     work during migration too, so it tolerates the missing field here.

All path functions are pure (return ``Path`` without ``mkdir``) except
``ai_hats_dir`` / ``traces_dir`` / ``pipeline_steps_dir``, which preserve
their historical ``mkdir -p`` behavior so callers don't have to guard.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml

LegacyClass = Literal["sessions", "tracker", "library", "root"]

# ---------- Base resolver ----------


def _read_ai_hats_dir_from_yaml(project_dir: Path) -> str | None:
    """Read raw ``ai_hats_dir`` field from ``ai-hats.yaml``.

    Bootstrap helper used by :func:`ai_hats_dir`. Does NOT trigger schema
    migration — that lives in ``ProjectConfig.from_yaml``. Returns ``None``
    if the file is missing, unreadable, or the field is absent/empty.
    """
    yaml_path = project_dir / "ai-hats.yaml"
    if not yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return None
    val = data.get("ai_hats_dir")
    return val if isinstance(val, str) and val else None


def _read_venv_path_from_yaml(project_dir: Path) -> str | None:
    """Read raw ``venv_path`` field from ``ai-hats.yaml`` (HATS-334).

    Low-level reader mirroring :func:`_read_ai_hats_dir_from_yaml`. Avoids
    pydantic so the bash launcher (HATS-339) and any hot-path callers have a
    consistent precedence spec to mirror. Returns ``None`` if file missing,
    unreadable, or field absent/empty.
    """
    yaml_path = project_dir / "ai-hats.yaml"
    if not yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return None
    val = data.get("venv_path")
    return val if isinstance(val, str) and val else None


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

    Other ``Path.home()`` usages in the codebase (e.g. ``~/.claude/``
    skills marker, expanding user-supplied ``~`` in CLI paths) are
    NOT covered by this override — they're not ai-hats-managed global
    state.
    """
    raw = os.environ.get("AI_HATS_USER_HOME")
    return Path(raw).expanduser() if raw else Path.home()


def ai_hats_dir(project_dir: Path) -> Path:
    """Base dir for ai-hats managed artefacts.

    Precedence: ``AI_HATS_DIR`` env > yaml ``ai_hats_dir`` > bootstrap
    fallback ``.agent/ai-hats/``. Created with ``mkdir -p`` so callers
    never have to guard.
    """
    raw = os.environ.get("AI_HATS_DIR")
    if raw:
        base = Path(raw).expanduser()
    else:
        yaml_value = _read_ai_hats_dir_from_yaml(project_dir)
        base = (project_dir / yaml_value) if yaml_value else (project_dir / ".agent" / "ai-hats")
    base.mkdir(parents=True, exist_ok=True)
    return base


def traces_dir(project_dir: Path) -> Path:
    """Pipeline trace JSONL directory: ``<ai_hats_dir>/traces/``."""
    d = ai_hats_dir(project_dir) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pipeline_steps_dir(project_dir: Path) -> Path:
    """User-authored pipeline-step modules: ``<ai_hats_dir>/pipeline_steps/``.

    Modules placed here are auto-imported by ``PipelineHarness`` on entry;
    see ``pipeline.user_steps.load_user_steps``.
    """
    d = ai_hats_dir(project_dir) / "pipeline_steps"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- Sessions class ----------


def sessions_dir(project_dir: Path) -> Path:
    """Session-class root: ``<ai_hats_dir>/sessions/``."""
    return ai_hats_dir(project_dir) / "sessions"


def runs_dir(project_dir: Path) -> Path:
    """Pipeline/session-trace runtime artefacts: ``<ai_hats_dir>/sessions/runs/``.

    Holds both ``pipeline_runs/<pipeline>/<session_id>/`` subtrees and
    flat ``session_<id>/`` trace dirs (legacy ``.gitlog/`` content).
    """
    return sessions_dir(project_dir) / "runs"


def retros_dir(project_dir: Path) -> Path:
    """Retrospectives: ``<ai_hats_dir>/sessions/retros/``."""
    return sessions_dir(project_dir) / "retros"


def audits_dir(project_dir: Path) -> Path:
    """Audit reports: ``<ai_hats_dir>/sessions/audits/``."""
    return sessions_dir(project_dir) / "audits"


def handoffs_dir(project_dir: Path) -> Path:
    """Context handoffs: ``<ai_hats_dir>/sessions/handoffs/``."""
    return sessions_dir(project_dir) / "handoffs"


def experiments_dir(project_dir: Path) -> Path:
    """Experiment artefacts: ``<ai_hats_dir>/sessions/experiments/``."""
    return sessions_dir(project_dir) / "experiments"


def worktrees_dir(project_dir: Path) -> Path:
    """Per-task worktree metadata: ``<ai_hats_dir>/sessions/worktrees/``."""
    return sessions_dir(project_dir) / "worktrees"


def worktree_state_path(project_dir: Path) -> Path:
    """Worktree state index: ``<ai_hats_dir>/sessions/worktree.json``."""
    return sessions_dir(project_dir) / "worktree.json"


# ---------- Tracker class ----------


def tracker_dir(project_dir: Path) -> Path:
    """Tracker-class root: ``<ai_hats_dir>/tracker/``."""
    return ai_hats_dir(project_dir) / "tracker"


def backlog_dir(project_dir: Path) -> Path:
    """Backlog root: ``<ai_hats_dir>/tracker/backlog/``."""
    return tracker_dir(project_dir) / "backlog"


def tasks_dir(project_dir: Path) -> Path:
    """Task cards: ``<ai_hats_dir>/tracker/backlog/tasks/``."""
    return backlog_dir(project_dir) / "tasks"


def proposals_dir(project_dir: Path) -> Path:
    """Proposals: ``<ai_hats_dir>/tracker/backlog/proposals/``."""
    return backlog_dir(project_dir) / "proposals"


def hypotheses_dir(project_dir: Path) -> Path:
    """Hypotheses: ``<ai_hats_dir>/tracker/hypotheses/``."""
    return tracker_dir(project_dir) / "hypotheses"


def decisions_dir(project_dir: Path) -> Path:
    """ADRs: ``<ai_hats_dir>/tracker/decisions/``."""
    return tracker_dir(project_dir) / "decisions"


def state_md_path(project_dir: Path) -> Path:
    """State index: ``<ai_hats_dir>/STATE.md``."""
    return ai_hats_dir(project_dir) / "STATE.md"


# ---------- Session cache (HATS-294) ----------


def session_cache_root(project_dir: Path) -> Path:
    """Root dir for per-session ephemeral artefacts: ``<ai_hats_dir>/.cache/sessions/``.

    Each session keeps its composed prompt and plugin-dir under
    ``<root>/<session_id>/``. The whole ``.cache/`` tree is gitignored
    and swept by TTL on session_start.
    """
    return ai_hats_dir(project_dir) / ".cache" / "sessions"


def session_cache_dir(project_dir: Path, session_id: str) -> Path:
    """Per-session cache dir: ``<ai_hats_dir>/.cache/sessions/<session_id>/``."""
    return session_cache_root(project_dir) / session_id


# ---------- Library class ----------


def library_dir(project_dir: Path) -> Path:
    """Library-class root: ``<ai_hats_dir>/library/``."""
    return ai_hats_dir(project_dir) / "library"


def rules_dir(project_dir: Path) -> Path:
    """Managed rules mirror: ``<ai_hats_dir>/library/rules/``."""
    return library_dir(project_dir) / "rules"


def skills_dir(project_dir: Path) -> Path:
    """Managed skills mirror: ``<ai_hats_dir>/library/skills/``."""
    return library_dir(project_dir) / "skills"


def hooks_dir(project_dir: Path) -> Path:
    """Canonical hooks source: ``<ai_hats_dir>/library/hooks/``."""
    return library_dir(project_dir) / "hooks"


# ---------- Framework-root artefacts ----------


def last_backup_path(project_dir: Path) -> Path:
    """Last-backup pointer: ``<ai_hats_dir>/.last_backup``."""
    return ai_hats_dir(project_dir) / ".last_backup"


def venv_path(project_dir: Path) -> Path:
    """Resolve ai-hats venv location (HATS-334).

    Precedence chain:
      1. ``AI_HATS_VENV`` env var — absolute path, runtime override (tests,
         sandbox, CI shared cache). ``~`` is expanded.
      2. yaml ``venv_path`` — relative (resolved against ``project_dir``)
         or absolute. Validated by :func:`normalize_venv_path`.
      3. Default ``<ai_hats_dir>/.venv``.

    Returns the absolute path without ``mkdir`` — venv creation is owned
    by ``bash bootstrap`` / ``self update`` (HATS-339), not by callers.
    """
    raw_env = os.environ.get("AI_HATS_VENV")
    if raw_env:
        return Path(raw_env).expanduser()
    raw_yaml = _read_venv_path_from_yaml(project_dir)
    if raw_yaml:
        p = Path(raw_yaml).expanduser()
        return p if p.is_absolute() else (project_dir / p)
    return ai_hats_dir(project_dir) / ".venv"


# ---------- Legacy migration helpers (consumed by HATS-312/313/314) ----------

# Maps a legacy path (relative to project_dir) to (class, new path
# relative to ai_hats_dir). The class lets HATS-312/313/314 pull only
# their slice; the relative new path is joined with ai_hats_dir at call
# time so env/yaml overrides apply.
LEGACY_PATH_MAP: dict[str, tuple[LegacyClass, str]] = {
    # Sessions — `.gitlog/` holds both pipeline_runs/ and session_<id>/
    # subdirs; the whole tree moves to sessions/runs/ in one shot.
    ".gitlog":                ("sessions", "sessions/runs"),
    ".agent/retrospectives":  ("sessions", "sessions/retros"),
    ".agent/audits":          ("sessions", "sessions/audits"),
    ".agent/handoffs":        ("sessions", "sessions/handoffs"),
    ".agent/experiments":     ("sessions", "sessions/experiments"),
    ".agent/worktrees":       ("sessions", "sessions/worktrees"),
    ".agent/worktree.json":   ("sessions", "sessions/worktree.json"),
    # Tracker
    ".agent/backlog":         ("tracker",  "tracker/backlog"),
    ".agent/hypotheses":      ("tracker",  "tracker/hypotheses"),
    ".agent/decisions":       ("tracker",  "tracker/decisions"),
    ".agent/STATE.md":        ("tracker",  "STATE.md"),
    # Library
    ".agent/rules":           ("library",  "library/rules"),
    ".agent/skills":          ("library",  "library/skills"),
    ".agent/hooks":           ("library",  "library/hooks"),
    # Framework root
    ".agent/.last_backup":    ("root",     ".last_backup"),
}


def detect_legacy_state(project_dir: Path) -> list[tuple[Path, Path]]:
    """Return ``[(old_abs, new_abs)]`` for every legacy path that exists.

    Used by HATS-312/313/314 inside ``Assembler.bump`` to drive the
    one-shot migration. Does NOT perform any move — callers do that for
    their class.
    """
    base = ai_hats_dir(project_dir)
    out: list[tuple[Path, Path]] = []
    for legacy, (_, new_rel) in LEGACY_PATH_MAP.items():
        old_abs = project_dir / legacy
        if old_abs.exists():
            out.append((old_abs, base / new_rel))
    return out


def legacy_paths_by_class(
    project_dir: Path,
    class_: LegacyClass,
) -> list[tuple[Path, Path]]:
    """Filtered :func:`detect_legacy_state` — one class only."""
    base = ai_hats_dir(project_dir)
    out: list[tuple[Path, Path]] = []
    for legacy, (c, new_rel) in LEGACY_PATH_MAP.items():
        if c != class_:
            continue
        old_abs = project_dir / legacy
        if old_abs.exists():
            out.append((old_abs, base / new_rel))
    return out


# ---------- Config-value validation (used by ProjectConfig validator) ----------


def normalize_ai_hats_dir(value: str) -> str:
    """Validate + normalize an ``ai_hats_dir`` config value.

    Raises ``ValueError`` on:
      - empty string, ``"."``, ``"/"``
      - absolute paths (project must be relocatable)
      - ``..`` segments (escape out of project)

    Normalization: POSIX-style separators, trailing slash stripped.
    """
    if not value:
        raise ValueError("ai_hats_dir must not be empty")
    p = PurePosixPath(value.replace("\\", "/"))
    if p.is_absolute():
        raise ValueError("ai_hats_dir must be relative to project root (not absolute)")
    if ".." in p.parts:
        raise ValueError("ai_hats_dir must not contain '..' segments")
    s = p.as_posix().rstrip("/")
    if s in {"", ".", "/"}:
        raise ValueError(f"ai_hats_dir is invalid: {value!r}")
    return s


def normalize_venv_path(value: str) -> str:
    """Validate + normalize a ``venv_path`` config value (HATS-334).

    Differs from :func:`normalize_ai_hats_dir` by ALLOWING absolute paths —
    venv may legitimately live outside the project (CI shared cache,
    system-wide ai-hats venv, user-owned override venv).

    Raises ``ValueError`` on:
      - empty string, ``"."``, ``"/"``
      - ``..`` segments (relative escape; not meaningful for absolute either)

    Normalization: POSIX-style separators, trailing slash stripped.
    """
    if not value:
        raise ValueError("venv_path must not be empty")
    p = PurePosixPath(value.replace("\\", "/"))
    if ".." in p.parts:
        raise ValueError("venv_path must not contain '..' segments")
    s = p.as_posix().rstrip("/")
    if s in {"", ".", "/"}:
        raise ValueError(f"venv_path is invalid: {value!r}")
    return s
