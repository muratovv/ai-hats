"""Directory-path resolution for ai-hats runtime + user config (HATS-316).

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
     from a v4 yaml; this resolver needs to work during migration too, so it
     tolerates the missing field here.

All path functions are pure (return ``Path`` without ``mkdir``). Directory
creation is the job of ``ensure_ai_hats_dir`` — the one validated creator that
refuses a non-project root — or a sanctioned explicit ``mkdir`` at a write site
(HATS-839).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Literal

import yaml

from .constants import PROJECT_CONFIG, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV

LegacyClass = Literal["sessions", "tracker", "library", "root"]

# Pair var pinned alongside AI_HATS_DIR at session spawn — scopes the pin
# to the project it was resolved for (HATS-897).
AI_HATS_PROJECT_DIR_ENV = "AI_HATS_PROJECT_DIR"

# ---------- Base resolver ----------


def _read_ai_hats_dir_from_yaml(project_dir: Path) -> str | None:
    """Read raw ``ai_hats_dir`` field from ``ai-hats.yaml``.

    Bootstrap helper used by :func:`ai_hats_dir`. Does NOT trigger schema
    migration — that lives in ``ProjectConfig.from_yaml``. Returns ``None``
    if the file is missing, unreadable, or the field is absent/empty.
    """
    yaml_path = project_dir / PROJECT_CONFIG
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
    yaml_path = project_dir / PROJECT_CONFIG
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


class NotAnAiHatsProjectError(Exception):
    """``project_dir`` is not an onboarded ai-hats project (HATS-839).

    Raised by :func:`ensure_ai_hats_dir` instead of letting a write bootstrap a
    phantom ``.agent/ai-hats`` skeleton at a wrong-but-alive root (the id-collision
    engine behind HATS-788). The CLI renders it as a friendly recovery message.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        super().__init__(
            f"{project_dir} is not an ai-hats project (no .agent/ or ai-hats.yaml, "
            "and AI_HATS_DIR is unset). cd to your project root, or run "
            "`ai-hats init` to onboard it."
        )


def _env_ai_hats_dir(project_dir: Path) -> Path | None:
    """``AI_HATS_DIR`` env override, scoped by its ``AI_HATS_PROJECT_DIR`` pair.

    HATS-897: wrap pins both vars at spawn; a pair leaked into another
    project's shell must not redirect that project's writes — on pin mismatch
    the override is ignored (+warn). A bare ``AI_HATS_DIR`` without the pair
    keeps its historical env-wins semantics.
    """
    raw = os.environ.get(ENV_AI_HATS_DIR)
    if not raw:
        return None
    pin = os.environ.get(AI_HATS_PROJECT_DIR_ENV)
    if pin and Path(pin).expanduser().resolve() != project_dir.resolve():
        warnings.warn(
            f"AI_HATS_DIR={raw!r} is pinned to project {pin!r} — foreign to "
            f"{project_dir}; ignoring the leaked session pin (HATS-897).",
            stacklevel=4,
        )
        return None
    return Path(raw).expanduser()


def _resolve_ai_hats_base(project_dir: Path) -> Path:
    """Pure precedence resolution of the base dir — NO mkdir.

    ``AI_HATS_DIR`` env (pair-scoped, HATS-897) > yaml ``ai_hats_dir`` >
    bootstrap ``.agent/ai-hats``. Shared by the pure :func:`ai_hats_dir` and
    the validating :func:`ensure_ai_hats_dir`.
    """
    env_base = _env_ai_hats_dir(project_dir)
    if env_base is not None:
        return env_base
    yaml_value = _read_ai_hats_dir_from_yaml(project_dir)
    return (project_dir / yaml_value) if yaml_value else (project_dir / ".agent" / "ai-hats")


def _is_ai_hats_project(project_dir: Path) -> bool:
    """True iff ``project_dir`` is an onboarded ai-hats project, or ``AI_HATS_DIR`` opts in.

    Markers: ``AI_HATS_DIR`` env (explicit runtime opt-in; pair-scoped per
    HATS-897 — a leaked foreign pin does not opt in), a pre-existing
    ``ai-hats.yaml``, or a pre-existing ``.agent/`` dir. A stray root has none.
    """
    if _env_ai_hats_dir(project_dir) is not None:
        return True
    if (project_dir / PROJECT_CONFIG).exists():
        return True
    return (project_dir / ".agent").is_dir()


def ai_hats_dir(project_dir: Path) -> Path:
    """Resolve the base dir for ai-hats managed artefacts — PURE, no mkdir (HATS-839).

    Precedence: ``AI_HATS_DIR`` env > yaml ``ai_hats_dir`` > bootstrap
    ``.agent/ai-hats/``. Returns the path WITHOUT creating it, so resolving against a
    wrong-but-alive root can never bootstrap a phantom tracker. Creation is the job of
    :func:`ensure_ai_hats_dir` (validated) or a sanctioned explicit ``mkdir``.
    """
    return _resolve_ai_hats_base(project_dir)


def ensure_ai_hats_dir(project_dir: Path) -> Path:
    """Validate ``project_dir`` is an ai-hats project, then create + return the base.

    The single sanctioned creator of the base dir (HATS-839). Unlike the pure
    :func:`ai_hats_dir`, this ``mkdir``s — but only for a real project
    (:func:`_is_ai_hats_project`); a stray root raises
    :class:`NotAnAiHatsProjectError` so no phantom tracker is bootstrapped. Call it
    at the top of every write op before touching the tracker.
    """
    if not _is_ai_hats_project(project_dir):
        raise NotAnAiHatsProjectError(project_dir)
    base = _resolve_ai_hats_base(project_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def traces_dir(project_dir: Path) -> Path:
    """Pipeline trace JSONL directory: ``<ai_hats_dir>/traces/`` — pure, no mkdir (HATS-839)."""
    return ai_hats_dir(project_dir) / "traces"


def pipeline_steps_dir(project_dir: Path) -> Path:
    """User-authored pipeline-step modules: ``<ai_hats_dir>/pipeline_steps/``.

    Modules placed here are auto-imported by ``PipelineHarness`` on entry;
    see ``pipeline.user_steps.load_user_steps``. Pure — no mkdir (HATS-839).
    """
    return ai_hats_dir(project_dir) / "pipeline_steps"


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


def worktrees_dir(project_dir: Path) -> Path:
    """Per-task worktree metadata: ``<ai_hats_dir>/sessions/worktrees/``."""
    return sessions_dir(project_dir) / "worktrees"


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


# ---------- Library class (materialized mirror) ----------


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


def wt_hooks_dir(project_dir: Path) -> Path:
    """Managed worktree-hook scripts: ``<ai_hats_dir>/library/wt-hooks/`` (HATS-823).

    Separate dir + manifest from :func:`hooks_dir` so the two sweeps never cross.
    """
    return library_dir(project_dir) / "wt-hooks"


def managed_wt_hook_filename(skill_name: str, script: str) -> str:
    """Collision-free on-disk filename for a worktree-hook script: ``<skill>-<basename>``.

    Both parts are reduced to ``Path(...).name`` so a tampered persisted carry
    cannot traverse out of :func:`wt_hooks_dir` at teardown (HATS-823 review).
    """
    return f"{Path(skill_name).name}-{Path(script).name}"


def managed_runtime_hook_filename(skill_name: str, script: str) -> str:
    """Collision-free on-disk filename for a skill-declared runtime-hook script.

    Single source of truth shared by the assembler (which materializes the
    file under :func:`hooks_dir`) and ``ClaudeProvider.ensure_runtime_hooks``
    (which writes the same path as the settings.json ``command``). The two
    sides MUST agree byte-for-byte — any drift means settings.json points at a
    script that was never written (the e2e catches this as an ``exit 127``).
    Mirrors the ``git_hooks`` dest convention (``<skill>-<basename>``);
    ``script`` may be a relpath — only its basename is used.
    """
    return f"{skill_name}-{Path(script).name}"


def user_hooks_dir(project_dir: Path) -> Path:
    """User-owned hooks sibling: ``<ai_hats_dir>/user-hooks/`` (HATS-549).

    Sibling of :func:`hooks_dir` but EXPLICITLY outside the
    ai-hats-managed namespace. Files relocated here by the v4 migration
    are project-authored; ai-hats does not sweep, materialize, or
    auto-wire them. Re-enabling a relocated hook is a manual
    user action — see the Stage B inventory entry written at migration
    time for the copy-paste snippet.

    The separation prevents the failure class observed in the proxmox
    regression: a user-authored ``.py`` cohabited with managed ``.sh``
    files under ``library/hooks/`` and got swept by a manifest-driven
    cleanup pass in an older ai-hats codepath. With the namespaces
    cleanly split, no managed sweep can ever touch user content.
    """
    return ai_hats_dir(project_dir) / "user-hooks"


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
    raw_env = os.environ.get(ENV_AI_HATS_VENV)
    if raw_env:
        return Path(raw_env).expanduser()
    raw_yaml = _read_venv_path_from_yaml(project_dir)
    if raw_yaml:
        p = Path(raw_yaml).expanduser()
        return p if p.is_absolute() else (project_dir / p)
    # HATS-647: managed blue-green resolution. When versions/current
    # resolves to a present versions/<sha>/, that is the active venv;
    # otherwise fall back to the legacy default .venv (lazy migration —
    # existing single-venv installs keep working until the first managed
    # `self update` populates versions/).
    sha = read_current_sha(project_dir)
    if sha is not None:
        return version_dir(project_dir, sha)
    return ai_hats_dir(project_dir) / ".venv"


# ---------- Versioned install layout (HATS-647) ----------


def _is_safe_sha_component(raw: str) -> bool:
    """True if ``raw`` is a single safe path component usable as a dir name.

    A managed ``sha`` is the git commit the active ai-hats was installed
    from (PEP 610 ``vcs_info.commit_id``) — hex, but we also tolerate
    tag/branch-derived names, so the alphabet is ``[A-Za-z0-9._-]``. Reject
    empty / dot / dotdot / anything with a path separator so a corrupt
    pointer can never escape ``versions/`` or be treated as valid.
    """
    if not raw or raw in (".", ".."):
        return False
    return all(c.isalnum() or c in "._-" for c in raw)


def versions_root(project_dir: Path) -> Path:
    """Root of blue-green versioned venvs: ``<ai_hats_dir>/versions/``.

    Pure path helper — no ``mkdir``. Directory creation is owned by
    ``self update`` / the bash launcher (mirrors :func:`venv_path`'s
    HATS-339 contract), not by read-side callers.
    """
    return ai_hats_dir(project_dir) / "versions"


def version_dir(project_dir: Path, sha: str) -> Path:
    """Per-``sha`` managed venv: ``<ai_hats_dir>/versions/<sha>/``."""
    return versions_root(project_dir) / sha


def current_pointer(project_dir: Path) -> Path:
    """Active-version pointer file: ``<ai_hats_dir>/versions/current``.

    Holds the active ``sha`` as a single line of text. A pointer-file (not
    a symlink) is portable to Windows / Git-Bash, where symlink creation
    needs elevated privileges. Flipped atomically (tmp+rename) by
    ``self update`` only after a successful install.
    """
    return versions_root(project_dir) / "current"


def complete_sentinel(project_dir: Path, sha: str) -> Path:
    """Completion marker for a managed venv: ``versions/<sha>/.complete``.

    Written **last** by ``self update`` — only after a fully-successful
    install+verify — so its presence is the authoritative "this install
    finished" signal (HATS-648). Independent of pip's internal file-write
    ordering: a build killed mid-pip can drop interpreter / package files yet
    lack ``.complete``. (HATS-790 removed the old ``bin/ai-hats`` console-script
    proxy this note used to cite; the ``.complete`` sentinel remains the
    completeness authority.)
    """
    return version_dir(project_dir, sha) / ".complete"


def is_complete(project_dir: Path, sha: str) -> bool:
    """True iff ``versions/<sha>/`` carries the ``.complete`` sentinel.

    The single completeness predicate (HATS-648): a ``<sha>`` dir without the
    sentinel is crash residue — **never trust dir-presence alone.** Used by
    :func:`read_current_sha` (completeness gate) and the recovery sweep
    (`version_recovery`), which removes only incomplete residue and leaves
    complete dirs to R2's liveness-based reclaim.
    """
    return complete_sentinel(project_dir, sha).is_file()


def is_usable_version(project_dir: Path, sha: str) -> bool:
    """True iff ``versions/<sha>/`` is a COMPLETE and RUNNABLE managed venv.

    Stronger than :func:`is_complete`. A *usable* version is one that can
    actually execute: it carries the ``.complete`` sentinel **and** has a
    runnable interpreter (``bin/python``) on disk, since the launcher now execs
    ``<venv>/bin/python -m ai_hats`` (HATS-790).

    The ``bin/python`` clause is HATS-657: a host python upgrade leaves a venv
    *complete* (sentinel present) yet *unrunnable* — its ``bin/python`` symlink
    dangles to the removed interpreter. Treating such a venv as current would
    make ``self update`` see ``already_current`` and skip the heal (#1) and
    false-fire the HATS-655 dormancy advisory (#2).

    HATS-790 (Alt 5) removed the ``[project.scripts] ai-hats`` console script,
    so a managed venv no longer materialises ``bin/ai-hats``; the entry point is
    importability of the package, which a complete venv with a live interpreter
    guarantees. Hence usability keys on ``bin/python`` alone — dropping the old
    ``bin/ai-hats`` clause, behaviour-equivalent for any real install.

    This is the single "usable" predicate, shared by :func:`read_current_sha`
    and the ``self update`` reuse gate, mirroring the launcher's ``-x bin/python``
    resolution so launcher, ``read_current_sha`` and the reuse path all agree on
    the same definition. A dangling symlink is ``False`` under both
    ``Path.exists()`` (it follows the link) and bash ``-x``, so the two stay
    consistent on the load-bearing case.
    """
    vdir = version_dir(project_dir, sha)
    return is_complete(project_dir, sha) and (vdir / "bin" / "python").exists()


def read_current_sha(project_dir: Path) -> str | None:
    """Resolve the active managed ``sha`` from ``versions/current``.

    Returns the ``sha`` only when the pointer exists, is well-formed, and the
    ``versions/<sha>/`` venv is **usable** — complete (carries the ``.complete``
    sentinel, HATS-648) AND runnable (``bin/python`` present, HATS-657 /
    HATS-790). A missing/corrupt pointer, a dangling ``sha``, an incomplete
    install, or a present-but-broken venv (e.g. a host python upgrade dangling
    ``bin/python``) returns ``None`` so callers fall back to the legacy ``.venv``
    (HATS-647 lazy-migration contract) — a corrupted versioned install degrades
    to the self-healing default rather than dead-ending.
    """
    try:
        raw = current_pointer(project_dir).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not _is_safe_sha_component(raw):
        return None
    # Usability is the single gate (HATS-657): sentinel (completeness authority,
    # written last post-verify — HATS-648) + bin/python (interpreter — the
    # launcher execs `bin/python -m ai_hats`, HATS-790). The corruption guard
    # (HATS-647 review-finding #1) folds in here: a complete-but-later-broken venv
    # — a python upgrade dangling bin/python — is NOT usable, so we return None and callers
    # degrade to the legacy .venv when it still exists. NOTE (HATS-653 / Phase B):
    # once a healthy versioned install is the authoritative venv, that legacy .venv
    # is deliberately reclaimed (reclaim_legacy_venv) — so post-reclaim this
    # degrade path has no fallback target and the launcher fails loud with
    # "Run: ai-hats self update", which heal_if_needed then rebuilds.
    if not is_usable_version(project_dir, raw):
        return None
    return raw


# ---------- Legacy migration helpers (consumed by HATS-312/313/314) ----------

# Maps a legacy path (relative to project_dir) to (class, new path
# relative to ai_hats_dir). The class lets HATS-312/313/314 pull only
# their slice; the relative new path is joined with ai_hats_dir at call
# time so env/yaml overrides apply.
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
    project_dir: Path,
    class_: LegacyClass,
) -> list[tuple[Path, Path]]:
    """Return ``[(old_abs, new_abs)]`` for legacy paths of one class only.

    Used by HATS-312/313/314 inside ``Assembler.bump`` to drive the one-shot
    migration: each caller pulls only its class. Does NOT perform any move.
    """
    base = ai_hats_dir(project_dir)
    out: list[tuple[Path, Path]] = []
    for legacy, (c, new_rel) in LEGACY_PATH_MAP.items():
        if c != class_:
            continue
        old_abs = project_dir / legacy
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
    return Path(url[len(prefix):]) if url.startswith(prefix) else None


__all__ = [
    "LegacyClass",
    "editable_install_root",
    "AI_HATS_PROJECT_DIR_ENV",
    "_read_ai_hats_dir_from_yaml",
    "_read_venv_path_from_yaml",
    "_is_safe_sha_component",
    "user_home",
    "ai_hats_dir",
    "NotAnAiHatsProjectError",
    "ensure_ai_hats_dir",
    "_resolve_ai_hats_base",
    "_is_ai_hats_project",
    "traces_dir",
    "pipeline_steps_dir",
    "sessions_dir",
    "runs_dir",
    "retros_dir",
    "audits_dir",
    "handoffs_dir",
    "worktrees_dir",
    "tracker_dir",
    "backlog_dir",
    "tasks_dir",
    "proposals_dir",
    "hypotheses_dir",
    "decisions_dir",
    "state_md_path",
    "session_cache_root",
    "session_cache_dir",
    "library_dir",
    "rules_dir",
    "skills_dir",
    "hooks_dir",
    "wt_hooks_dir",
    "managed_wt_hook_filename",
    "managed_runtime_hook_filename",
    "user_hooks_dir",
    "last_backup_path",
    "venv_path",
    "versions_root",
    "version_dir",
    "current_pointer",
    "complete_sentinel",
    "is_complete",
    "is_usable_version",
    "read_current_sha",
    "LEGACY_PATH_MAP",
    "legacy_paths_by_class",
]
