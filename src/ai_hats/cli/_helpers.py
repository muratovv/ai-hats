"""Shared helpers used by ≥2 CLI modules."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import click
from rich.console import Console

if TYPE_CHECKING:
    from ..pipeline.steps.compose import RoleNotFoundError

console = Console()
logger = logging.getLogger(__name__)


def _handle_role_not_found(exc: "RoleNotFoundError") -> NoReturn:
    """Render a `RoleNotFoundError` as a friendly stderr message and exit 2.

    Single source of truth for the unknown-role UX shared by every CLI
    entry-point that runs the ``compose_role`` pipeline step (bare
    ``ai-hats``, ``ai-hats execute``, ``ai-hats agent``, ``ai-hats
    reflect *``). Before HATS-547 only ``_launch_session`` handled the
    typed exception; ``execute_cmd`` let it bubble up as a 9-frame
    traceback (S-CLI-20 — Wave 2 e2e gap).

    Output contract (asserted by
    ``tests/e2e/test_unknown_role_friendly_error.py``):

    - Names the typo'd role on stderr.
    - Lists every available role one-per-line under an
      ``Available roles:`` header.
    - Hints at ``ai-hats list roles`` for the full table.
    - Exits 2 (Click's UsageError convention; HATS-507 mirror).

    No ``Traceback`` ever reaches the user — that's the whole point of
    the typed-exception design and this helper.
    """
    click.echo(f"Error: Role {exc.role!r} not found.\n", err=True)
    click.echo("Available roles:", err=True)
    for name in exc.available:
        click.echo(f"  - {name}", err=True)
    click.echo("\nHint: 'ai-hats list roles' shows the full table.", err=True)
    sys.exit(2)


def exec_claude_with_retro(retro_path: Path, kind: str = "session") -> None:
    """Replace the current process with `claude` preloaded on a retro file.

    Builds an opening prompt that references the retro path and invites the
    user to discuss findings, then `os.execvp`s into the `claude` binary so
    the live chat takes over the terminal. Used by `--interactive` flag on
    `ai-hats session retro`.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]--interactive: 'claude' binary not found in PATH.[/] "
            "Install Claude Code or run the retro file through your editor.",
        )
        sys.exit(1)

    label = "session retro"
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


def _guard_not_inside_linked_worktree(project_dir: Path) -> None:
    """HATS-060 / HATS-482 (B-08): refuse `wt` ops issued from inside a linked
    worktree, except those designed to be run there (`wt exec`, `wt env`).

    Without this guard, `_project_dir` walks up through ``.agent/`` /
    ``.git/`` and — if the user shelled into a ``/tmp/ai-hats-wt-…`` dir
    without those markers — resolves to a parent that is NOT the real project
    root. CLI then writes state files under the tmp tree → corrupt /
    orphaned state on next agent invocation.

    Originally inline in ``wt_create`` (HATS-060). Lifted to a helper so
    ``wt_merge`` / ``wt_discard`` / ``wt_list`` share the same guard (B-08).

    Prints a guidance message and ``sys.exit(1)`` on breach. Returns None
    when CWD is OK (main worktree or non-git path).
    """
    from ..worktree import WorktreeManager

    if WorktreeManager.is_inside_linked_worktree(project_dir):
        console.print("[red]Cannot run this command from inside a linked worktree[/]")
        console.print(f"  You are in: {project_dir}")
        console.print("  Run from the main repo. To act on the active worktree without")
        console.print("  leaving it, use [bold]ai-hats wt exec[/] / [bold]ai-hats wt env[/].")
        sys.exit(1)


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
