"""Centralized environment variable access and public contract across ai-hats (HATS-1414).

Single source of truth for reading all ``os.environ`` variables.
Dependencies are restricted to standard library (``os``) to maintain absolute
leaf module purity (contract guarded by ``test_import_hygiene.py``).
"""

from __future__ import annotations

import os

# Env-var names read across ai-hats (HATS-917, HATS-1414)
ENV_AI_HATS_USER_HOME = "AI_HATS_USER_HOME"
ENV_AI_HATS_DIR = "AI_HATS_DIR"
AI_HATS_PROJECT_DIR_ENV = "AI_HATS_PROJECT_DIR"
ENV_AI_HATS_VENV = "AI_HATS_VENV"
ENV_LIBRARY_ROOT = "AI_HATS_LIBRARY_ROOT"
ENV_AI_HATS_CACHE_HOME = "AI_HATS_CACHE_HOME"
ENV_XDG_CACHE_HOME = "XDG_CACHE_HOME"
ENV_SESSION_CACHE_DIR = "AI_HATS_SESSION_CACHE_DIR"

# Session identity written at spawn (ADR-0025 D1). `AI_HATS_SESSION_ID` is
# absent on purpose — its home is `ai_hats_observe.trace` (HATS-948).
ENV_ROLE = "AI_HATS_ROLE"
ENV_ROOT_PID = "AI_HATS_ROOT_PID"
#: The session process's own interpreter. A second pin beside ENV_AI_HATS_VENV
#: because the agy global hook is invoked by the surface, not by our launcher.
ENV_AI_HATS_PYTHON = "AI_HATS_PYTHON"

# Hook-point vocabulary, owned by ADR-0020 D2; named here so it has one home.
ENV_HOOK_POINT = "AI_HATS_HOOK_POINT"
ENV_IN_HOOK = "AI_HATS_IN_HOOK"
ENV_FORCE = "AI_HATS_FORCE"
ENV_TASK_ID = "AI_HATS_TASK_ID"
ENV_WORKTREE_PATH = "AI_HATS_WORKTREE_PATH"
ENV_TASKS_DIR = "AI_HATS_TASKS_DIR"  # NOT rack's own RACK_TASKS_DIR
# Set only once the worktree is gone AND its branch reached the base branch, so
# it is the one signal separating "brought no code" from "already merged".
ENV_MERGED_SHA = "AI_HATS_MERGED_SHA"
#: The call envelope — per-CALL facts as one versioned JSON object,
#: BESIDE the scalars above, which shell keeps reading.
ENV_HOOK_CALL = "AI_HATS_HOOK_CALL"


def _read(name: str) -> str | None:
    """Read environment variable; empty string is treated as unset (None)."""
    return os.environ.get(name) or None


def user_home_override() -> str | None:
    """Read ``AI_HATS_USER_HOME`` env var.

    Meaning: Runtime override for the global user home directory (bypassing ``Path.home()``)
    for ai-hats managed global state (e.g. global user config and global library layer).
    Documentation: ``docs/how-to-configure.md`` (Global configuration).
    """
    return _read(ENV_AI_HATS_USER_HOME)


def ai_hats_dir_override() -> str | None:
    """Read ``AI_HATS_DIR`` env var.

    Meaning: Runtime override for the framework base directory (by default ``.agent/ai-hats``).
    Pair-scoped with ``AI_HATS_PROJECT_DIR`` to prevent leaked session pins across projects.
    Documentation: ``docs/how-to-configure.md`` (Directory resolution, HATS-897).
    """
    return _read(ENV_AI_HATS_DIR)


def project_dir_pin() -> str | None:
    """Read ``AI_HATS_PROJECT_DIR`` env var.

    Meaning: Project root pin set at session spawn alongside ``AI_HATS_DIR`` to validate
    override scoping and ignore foreign leaked environment variables.
    Documentation: HATS-897 (Leaked session pin guard).
    """
    return _read(AI_HATS_PROJECT_DIR_ENV)


def venv_override() -> str | None:
    """Read ``AI_HATS_VENV`` env var.

    Meaning: Absolute path runtime override for the Python virtual environment location.
    Documentation: ``docs/how-to-configure.md`` (Python environment resolution, HATS-334).
    """
    return _read(ENV_AI_HATS_VENV)


def library_root_override() -> str | None:
    """Read ``AI_HATS_LIBRARY_ROOT`` env var.

    Meaning: Environment override for the builtin library root directory containing
    the ``core`` and ``usage`` composition layers.
    Documentation: ``docs/architecture.md`` (Builtin library resolution, HATS-831).
    """
    return _read(ENV_LIBRARY_ROOT)


def tool_home_override(env_var: str) -> str | None:
    """Read arbitrary tool home environment variable ``env_var``.

    Meaning: Generic environment override for tool-specific home directory pattern (``~/.<name>``).
    Documentation: Shared transcript-discovery and tool-home resolution (HATS-1087).
    """
    return _read(env_var)


def cache_home_override() -> str | None:
    """Read ``AI_HATS_CACHE_HOME`` env var.

    Meaning: Runtime override for the BASE of the machine-only cache class, which lives
    outside the project. Never a project's final cache root — ``paths.cache_root`` always
    appends the per-project key, so a leaked value cannot merge two projects' caches.
    Documentation: ``docs/ARCHITECTURE.md`` (Materialization), HATS-1398.
    """
    return _read(ENV_AI_HATS_CACHE_HOME)


def xdg_cache_home() -> str | None:
    """Read ``XDG_CACHE_HOME`` env var.

    Meaning: Platform cache base; ai-hats appends ``ai-hats/`` to it. Ranks below
    ``AI_HATS_CACHE_HOME`` and above ``user_home()`` when resolving the cache class.
    Documentation: ``docs/ARCHITECTURE.md`` (Materialization), HATS-1398.
    """
    return _read(ENV_XDG_CACHE_HOME)


__all__ = [
    "ENV_AI_HATS_USER_HOME",
    "ENV_AI_HATS_DIR",
    "AI_HATS_PROJECT_DIR_ENV",
    "ENV_AI_HATS_VENV",
    "ENV_LIBRARY_ROOT",
    "ENV_AI_HATS_CACHE_HOME",
    "ENV_XDG_CACHE_HOME",
    "ENV_SESSION_CACHE_DIR",
    "ENV_ROLE",
    "ENV_ROOT_PID",
    "ENV_AI_HATS_PYTHON",
    "ENV_HOOK_POINT",
    "ENV_IN_HOOK",
    "ENV_FORCE",
    "ENV_TASK_ID",
    "ENV_WORKTREE_PATH",
    "ENV_TASKS_DIR",
    "ENV_MERGED_SHA",
    "ENV_HOOK_CALL",
    "user_home_override",
    "ai_hats_dir_override",
    "project_dir_pin",
    "venv_override",
    "library_root_override",
    "tool_home_override",
    "cache_home_override",
    "xdg_cache_home",
]
