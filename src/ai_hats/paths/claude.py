"""Claude Code path conventions — the single home for ``.claude/*`` coupling.

ai-hats depends on an external tool's on-disk layout; this module names that
dependency explicitly instead of scattering literals (HATS-907 review).

TODO(HATS-908): migrate the remaining ``.claude/*`` deps here —
``CLAUDE_PROJECT_DIR_VAR`` / ``strip_claude_project_dir`` (``_dirs.py``),
settings.json paths (``providers.py``), the publish manifest + CLAUDE.md
scaffolding (``assembler.py``), ``runtime_common`` literals.
"""

from __future__ import annotations

from pathlib import Path


AI_HATS_MANAGED_MARKER = ".ai-hats-managed"


def claude_dir(base: Path) -> Path:
    """Claude Code's config dir under ``base``: ``.claude/``."""
    return base / ".claude"


def claude_skills_dir(base: Path) -> Path:
    """Claude Code's skill auto-discovery dir under ``base``: ``.claude/skills/``.

    ``base`` is a project root or the user home — Claude Code scans both
    scopes (HATS-901/907).
    """
    return claude_dir(base) / "skills"


def claude_settings_json(base: Path) -> Path:
    """Claude Code's project settings file: ``.claude/settings.json``."""
    return claude_dir(base) / "settings.json"


__all__ = [
    "AI_HATS_MANAGED_MARKER",
    "claude_dir",
    "claude_settings_json",
    "claude_skills_dir",
]
