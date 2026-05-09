"""Path conventions for ai-hats runtime + user config.

Single source of truth for "where does ai-hats keep its files?". The base
namespace is ``<project>/.agent/ai-hats/`` (overridable via ``AI_HATS_DIR``
env var) and houses both:

  - runtime output (``traces/``  — HATS-274)
  - user-authored extension code (``pipeline_steps/`` — HATS-275)
  - future user-authored YAML pipelines (``pipelines/`` — HATS-268)

The ``AI_HATS_DIR`` override applies to all of them, so a team can
share a step-library by pointing ``AI_HATS_DIR`` at a common location.

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


def pipeline_steps_dir(project_dir: Path) -> Path:
    """User-authored pipeline-step modules: ``<ai_hats_dir>/pipeline_steps/``.

    Created (mkdir -p) so callers never have to guard. Modules placed
    here are auto-imported by ``PipelineHarness`` on entry; see
    ``pipeline.user_steps.load_user_steps``.
    """
    d = ai_hats_dir(project_dir) / "pipeline_steps"
    d.mkdir(parents=True, exist_ok=True)
    return d
