"""Path conventions for ai-hats runtime artefacts.

Single source of truth for "where does ai-hats keep its files?". The base
namespace is ``<project>/.agent/ai-hats/`` (overridable via ``AI_HATS_DIR``
env var). On HATS-274 this only houses pipeline traces; future refactors
may relocate sessions/audits/hooks here as well — `paths.py` is the
extension point.

`.agent/` is already gitignored, so the new namespace inherits that.
"""

from __future__ import annotations

import os
from pathlib import Path


def ai_hats_dir(project_dir: Path) -> Path:
    """Base dir for ai-hats runtime artefacts.

    Resolution order:
      1. ``AI_HATS_DIR`` env var (absolute or relative-to-cwd; ``~``
         is expanded).
      2. ``<project_dir>/.agent/ai-hats/`` (default).

    Created idempotently with ``mkdir -p`` on every call so callers
    never have to guard.
    """
    raw = os.environ.get("AI_HATS_DIR")
    base = Path(raw).expanduser() if raw else (project_dir / ".agent" / "ai-hats")
    base.mkdir(parents=True, exist_ok=True)
    return base


def traces_dir(project_dir: Path) -> Path:
    """Pipeline trace JSONL directory: ``<ai_hats_dir>/traces/``."""
    d = ai_hats_dir(project_dir) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    return d
