"""Agy CLI path conventions — the single home for Agy-tool coupling.

Symmetric to :mod:`.claude` (HATS-908): ai-hats depends on an external tool's
layout/channels; this module names that dependency explicitly. Agy (Antigravity)
reads a ``GEMINI.md`` memory file and a ``.agy/skills/`` registry; no settings
channel ai-hats touches.
"""

from __future__ import annotations

from pathlib import Path


GEMINI_MD_FILENAME = "GEMINI.md"


def gemini_md(project_dir: Path) -> Path:
    """Agy CLI's memory / system-prompt file: ``./GEMINI.md`` (Antigravity reads it)."""
    return project_dir / GEMINI_MD_FILENAME


def agy_skills_dir(project_dir: Path) -> Path:
    """Agy CLI's workspace skill registry: ``./.agy/skills/`` (HATS-993)."""
    return project_dir / ".agy" / "skills"


def gemini_settings_dir(project_dir: Path) -> Path:
    """Agy CLI's project configuration directory: ``./.gemini/``."""
    return project_dir / ".gemini"


def gemini_settings_path(project_dir: Path) -> Path:
    """Agy CLI's project settings file: ``./.gemini/settings.json``."""
    return gemini_settings_dir(project_dir) / "settings.json"


__all__ = [
    "GEMINI_MD_FILENAME",
    "gemini_md",
    "agy_skills_dir",
    "gemini_settings_dir",
    "gemini_settings_path",
]
