"""Runtime-layout injection seam for the standalone session-browse CLI (HATS-952).

The observe CLI (`session list/show/audit`) defaults to project-local, wt-free
resolvers so it runs with only ai-hats-core. The integrator overrides these
module globals at mount (`ai_hats.cli.__init__`) with its AI_HATS_DIR/yaml-aware
versions (`ai_hats.paths.runs_dir`, `_helpers._project_dir`/`console`,
`ai_hats.tags.parse_tag_filters`), restoring `ai-hats session`'s exact layout and
tag semantics. Reference the slots as ``_seam.<slot>`` (attribute access at call
time) so one integrator override reaches every importer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rich.console import Console


def _default_project_dir() -> Path:
    """Resolve the project root by walking up from CWD, wt-free.

    Prefer a directory holding ``.agent``; else the nearest ``.git`` holder;
    else CWD. Mirrors the tracker seam's ``_default_project_dir`` minus the
    linked-worktree hop (that hop needs ai-hats-wt).
    """
    cwd = Path.cwd()
    candidates = [cwd, *cwd.parents]
    for d in candidates:
        if (d / ".agent").is_dir():
            return d
    for d in candidates:
        git = d / ".git"
        if git.is_dir() or git.is_file():
            return d
    return cwd


def _default_runs_dir(project_dir: Path) -> Path:
    """Standalone session-runs dir: ``<project>/.agent/sessions/runs`` (wt-free).

    The integrator overrides with ``ai_hats.paths.runs_dir`` (AI_HATS_DIR/yaml
    precedence). This flat ``.agent`` layout deliberately differs from the
    integrator's ``<ai_hats_dir>/sessions/runs`` subtree — same rationale as the
    tracker's standalone ``.agent/<kind>`` dirs.
    """
    return project_dir / ".agent" / "sessions" / "runs"


def _default_tag_filter_parser(raw: Iterable[str]) -> dict[str, str]:
    """Minimal wt-free ``k=v`` tag-filter parser for standalone ``list --tag``.

    Splits each ``key=value`` on the first ``=``; raises ``ValueError`` on a
    missing ``=`` or empty key. The integrator overrides with
    ``ai_hats.tags.parse_tag_filters`` (strict format / reserved-key / length
    validation). ``ValueError`` is the shared contract the command catches
    (``ai_hats.tags.TagValidationError`` subclasses it).
    """
    filters: dict[str, str] = {}
    for item in raw:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"tag filter must be key=value, got {item!r}")
        filters[key] = value
    return filters


# Injectable slots — the integrator overrides these at mount (ai_hats.cli).
_PROJECT_DIR = _default_project_dir
_RUNS_DIR = _default_runs_dir
_TAG_FILTER_PARSER = _default_tag_filter_parser
_CONSOLE = Console()
