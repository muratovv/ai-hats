"""Single-home leaf for environment variable reading across ``paths`` (HATS-1414).

All ``os.environ`` access within the ``paths`` package is centralized here.
Dependencies are restricted to standard library (``os``) and ``.constants`` to
maintain the dependency-free leaf contract.
"""

from __future__ import annotations

import os

from .constants import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    ENV_AI_HATS_USER_HOME,
    ENV_AI_HATS_VENV,
    ENV_LIBRARY_ROOT,
)


def _read(name: str) -> str | None:
    """Env value; empty string means unset, as every caller assumes."""
    return os.environ.get(name) or None


def user_home_override() -> str | None:
    return _read(ENV_AI_HATS_USER_HOME)


def ai_hats_dir_override() -> str | None:
    return _read(ENV_AI_HATS_DIR)


def project_dir_pin() -> str | None:
    return _read(AI_HATS_PROJECT_DIR_ENV)


def venv_override() -> str | None:
    return _read(ENV_AI_HATS_VENV)


def library_root_override() -> str | None:
    return _read(ENV_LIBRARY_ROOT)


def tool_home_override(env_var: str) -> str | None:
    return _read(env_var)


__all__ = [
    "user_home_override",
    "ai_hats_dir_override",
    "project_dir_pin",
    "venv_override",
    "library_root_override",
    "tool_home_override",
]
