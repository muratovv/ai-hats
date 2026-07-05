"""Gemini CLI path conventions — the single home for Gemini-tool coupling.

Symmetric to :mod:`.claude` (HATS-908): ai-hats depends on an external tool's
layout/channels; this module names that dependency explicitly. Small surface
by design — Gemini CLI has no ``.gemini/`` project dir or settings channel
ai-hats touches.
"""

from __future__ import annotations

from pathlib import Path


GEMINI_MD_FILENAME = "GEMINI.md"

# Env channel Gemini CLI reads for a rules-dir override; ai-hats injects the
# composed prompt through it instead of touching ``./GEMINI.md``.
GEMINI_CLI_PROJECT_RULES_PATH_ENV = "GEMINI_CLI_PROJECT_RULES_PATH"


def gemini_md(project_dir: Path) -> Path:
    """Gemini CLI's memory / system-prompt file: ``./GEMINI.md``."""
    return project_dir / GEMINI_MD_FILENAME


__all__ = [
    "GEMINI_CLI_PROJECT_RULES_PATH_ENV",
    "GEMINI_MD_FILENAME",
    "gemini_md",
]
