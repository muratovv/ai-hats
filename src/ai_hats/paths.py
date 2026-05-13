"""Path conventions for ai-hats runtime + user config (HATS-316).

Single source of truth for "where does ai-hats keep its files?". All
framework-managed artefacts live under ``<ai_hats_dir>/`` — by default
``<project>/.agent/ai-hats/`` — split into three classes:

  - ``sessions/``  — per-run / runtime artefacts (runs, retros, audits,
    handoffs, experiments, worktrees).
  - ``tracker/``   — cards + records with lifecycle (backlog, hypotheses,
    decisions); plus ``STATE.md`` and ``.last_backup`` on the dir root.
  - ``library/``   — managed mirrors of the role composition (rules,
    skills, hooks, mcp) for external consumers.

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


def mcp_dir(project_dir: Path) -> Path:
    """MCP configs: ``<ai_hats_dir>/library/mcp/``."""
    return library_dir(project_dir) / "mcp"


# ---------- Framework-root artefacts ----------


def last_backup_path(project_dir: Path) -> Path:
    """Last-backup pointer: ``<ai_hats_dir>/.last_backup``."""
    return ai_hats_dir(project_dir) / ".last_backup"


def local_venv_path(project_dir: Path) -> Path:
    """Opt-in local Python venv for ai-hats CLI: ``<ai_hats_dir>/.venv/``.

    HATS-318. The path is purely a convention — existence of
    ``<venv>/bin/python`` is what activates the wrapper re-exec in
    :func:`ai_hats.cli.main_entry`.
    """
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
    ".agent/mcp":             ("library",  "library/mcp"),
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
