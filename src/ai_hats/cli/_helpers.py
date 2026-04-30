"""Shared helpers used by ≥2 CLI modules."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


def exec_claude_with_retro(retro_path: Path, kind: str = "session") -> None:
    """Replace the current process with `claude` preloaded on a retro file.

    Builds an opening prompt that references the retro path and invites the
    user to discuss findings, then `os.execvp`s into the `claude` binary so
    the live chat takes over the terminal. Used by `--interactive` flags on
    `ai-hats retro` and `ai-hats judge`.

    `kind` shapes the prompt — "session" for a session retro, "judge" for a
    forensic judge retro, "bundle" for an aggregated bundle retro.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]--interactive: 'claude' binary not found in PATH.[/] "
            "Install Claude Code or run the retro file through your editor.",
        )
        sys.exit(1)

    label = {"judge": "judge retro", "bundle": "bundle retro"}.get(kind, "session retro")
    rel = retro_path
    try:
        rel = retro_path.relative_to(Path.cwd())
    except ValueError:
        pass

    prompt = (
        f"Read {rel} — this is a {label} I just generated. "
        "Walk me through the findings, then we'll decide what to fix."
    )
    console.print(f"[cyan]→ Handing off to claude with {label}: {rel}[/]")
    os.execvp(claude_bin, [claude_bin, prompt])


def _project_dir() -> Path:
    """Resolve the project root by walking up from CWD.

    Order of preference:
      1. Nearest ancestor (incl. CWD itself) that contains `.agent/` —
         that ancestor IS the project root for this backlog.
      2. Nearest ancestor that contains `.git` (file or dir) — standard
         git-root semantics, used when the project hasn't been onboarded
         to ai-hats yet but the user is initializing it.
      3. Fallback: CWD (projects without VCS or pre-init scenarios).

    `.agent/` takes precedence over `.git/` so linked git worktrees that
    don't carry their own `.agent/` resolve up to the main project root,
    matching the user expectation that the backlog lives in one place per
    repo.
    """
    cwd = Path.cwd()
    candidates = [cwd, *cwd.parents]

    for d in candidates:
        if (d / ".agent").is_dir():
            return d

    for d in candidates:
        if (d / ".git").exists():
            return d

    return cwd


def _assembler(project_dir: Path | None = None):
    from ..assembler import Assembler

    return Assembler(project_dir or _project_dir())


def _task_manager(project_dir: Path | None = None):
    """Construct a TaskManager with the project's configured task-id prefix.

    Falls back to auto-detection (and persists the result) when the project
    has existing task folders but no `task_prefix` in ai-hats.yaml — keeps
    legacy repos on their historical prefix without manual migration.
    """
    from ..models import ProjectConfig
    from ..state import TaskManager

    pdir = project_dir or _project_dir()
    config_path = pdir / "ai-hats.yaml"
    prefix = ProjectConfig.resolve_task_prefix(pdir, config_path)
    return TaskManager(pdir, prefix=prefix)
