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


def claude_skills_dir(base: Path) -> Path:
    """Claude Code's skill auto-discovery dir under ``base``: ``.claude/skills/``.

    ``base`` is a project root or the user home — Claude Code scans both
    scopes (HATS-901/907).
    """
    return base / ".claude" / "skills"


__all__ = [
    "claude_skills_dir",
]
