"""Shared helpers used by ≥2 CLI modules."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn



import click
from rich.console import Console

from ..constants import is_debug_mode
from ..paths import PROJECT_CONFIG

if TYPE_CHECKING:
    from ..composition_seam import RoleNotFoundError
    from ..providers import UnknownProviderError

console = Console()
logger = logging.getLogger(__name__)


def _handle_role_not_found(exc: "RoleNotFoundError") -> NoReturn:
    """Render a `RoleNotFoundError` as a friendly stderr message and exit 2.

    Single source of truth for the unknown-role UX shared by every CLI
    entry-point that composes at the integrator seam (bare ``ai-hats``,
    ``ai-hats execute``, ``ai-hats agent``, ``ai-hats reflect *`` —
    HATS-865 moved the raise from the ``compose_role`` step to
    ``composition_seam.build_composition_payload``). Before HATS-547 only
    ``_launch_session`` handled the typed exception; ``execute_cmd`` let it
    bubble up as a 9-frame traceback (S-CLI-20 — Wave 2 e2e gap).

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


def _handle_unknown_provider(exc: "UnknownProviderError") -> NoReturn:
    """Render an ``UnknownProviderError`` as friendly stderr + exit 2.

    The provider analogue of ``_handle_role_not_found`` for the bare-launch
    surface (HATS-965): names the bad provider, lists registered ones, hints at
    ``ai-hats list providers``. No ``Traceback`` reaches the user. Output
    contract asserted by ``tests/e2e/test_unknown_provider_friendly_error.py``.
    """
    with catch_broken_install():
        from ..self_heal import get_surface_remediation

    click.echo(f"Error: Provider {exc.name!r} not found.\n", err=True)
    remediation = get_surface_remediation(exc.name)
    if remediation:
        click.echo(f"Surface provider {exc.name!r} is not installed.", err=True)
        click.echo(f"Fix: {remediation}\n", err=True)
    click.echo("Available providers:", err=True)
    for name in exc.available:
        click.echo(f"  - {name}", err=True)
    click.echo("\nHint: 'ai-hats list providers' shows the full table.", err=True)
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


class DeadCwdError(click.ClickException):
    """The current working directory no longer exists (HATS-788).

    Commonly: the linked worktree you were standing in was just torn down by
    `task transition done` / `wt merge`. Resolving the project root from a
    removed cwd would otherwise crash (`Path.cwd()` → FileNotFoundError on
    macOS) or, on Linux where `os.getcwd()` can return a stale path string,
    silently fall through to the cwd fallback and let `ai_hats_dir()`'s
    `mkdir -p` resurrect a phantom `.agent/` tracker. Fail loud instead and
    point the operator back to a real directory.
    """

    def __init__(self) -> None:
        super().__init__(
            "Current directory no longer exists (a worktree you were in may "
            "have just been removed). cd to your project root and re-run."
        )


class InconsistentInstallError(click.ClickException):
    """Raised when an internal import fails due to a broken or inconsistent install (HATS-1120)."""

    def __init__(self, exc: Exception) -> None:
        msg = (
            f"Inconsistent or broken ai-hats installation ({exc}).\n"
            "Likely cause: package files are out of sync or corrupted.\n"
            "Repair command: python -m ai_hats self update (or 'ai-hats self update')\n"
            "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v"
        )
        super().__init__(msg)

    def show(self, file=None) -> None:
        from ..startup_notices import show_fatal_notice_and_exit

        show_fatal_notice_and_exit(self.format_message(), exit_code=self.exit_code)


def _handle_broken_install_or_die(exc: Exception) -> NoReturn:
    """Handle an ImportError/module AttributeError at the CLI boundary and exit (HATS-1120).

    If debug/verbose mode is enabled or the exception is not a broken install symptom (HATS-1132),
    re-raises the original exception so the full traceback is displayed.
    Otherwise, renders InconsistentInstallError to stderr and exits with status 1 without a raw traceback.
    """
    from ..self_heal import is_broken_install_exception

    if is_debug_mode() or not is_broken_install_exception(exc):
        raise exc
    err = InconsistentInstallError(exc)
    err.show()
    sys.exit(err.exit_code)


@contextlib.contextmanager
def catch_broken_install():
    """Context manager wrapping CLI lazy imports to catch broken install errors (HATS-1120, HATS-1132)."""
    from ..self_heal import is_broken_install_exception

    try:
        yield
    except Exception as exc:
        if is_broken_install_exception(exc):
            _handle_broken_install_or_die(exc)
        raise




def _project_dir() -> Path:
    """Resolve the project root by walking up from CWD.

    Order of preference:
      1. Nearest ancestor (incl. CWD itself) that contains `.agent/` —
         that ancestor IS the project root for this backlog.
      2. Nearest ancestor that contains a `.git` **directory** — standard
         git-root semantics, used when the project hasn't been onboarded
         to ai-hats yet but the user is initializing it.
      3. Nearest ancestor whose `.git` is a **file** — a gitlink, typically a
         linked git worktree (HATS-524; a submodule's `.git` is also a file).
         The worktree checkout carries neither the gitignored `.agent/` nor the
         untracked `ai-hats.yaml`, and the main checkout is NOT a filesystem
         ancestor (worktrees live under /tmp), so pass 1 can never reach it.
         Hop to the main worktree root via git's commondir so `ai-hats task`
         ops route through the one live tracker. Anything that isn't a linked
         worktree (submodule, malformed pointer, git error) yields no hop and
         falls back to the dir holding the `.git` file.
      4. Fallback: CWD (projects without VCS or pre-init scenarios).

    `.agent/` takes precedence over `.git/` so a main checkout always resolves
    to itself without spawning git — the worktree hop in pass 3 only fires when
    no `.agent/` ancestor exists.
    """
    # HATS-788: fail loud on a removed cwd rather than crashing (macOS:
    # Path.cwd() raises FileNotFoundError) or silently resurrecting a phantom
    # tracker (Linux: os.getcwd() may return a stale path string for a removed
    # directory). A non-existent-but-returned path is treated the same.
    try:
        cwd = Path.cwd()
    except FileNotFoundError as exc:
        raise DeadCwdError() from exc
    if not cwd.exists():
        raise DeadCwdError()
    candidates = [cwd, *cwd.parents]

    for d in candidates:
        if (d / ".agent").is_dir():
            return d

    for d in candidates:
        git = d / ".git"
        if git.is_dir():
            return d
        if git.is_file():
            # Gitlink (linked worktree / submodule): for a linked worktree,
            # route to the main checkout's live tracker; otherwise fall back
            # to this dir (current behaviour, never worse).
            with catch_broken_install():
                from ai_hats_wt import WorktreeManager

            main_root = WorktreeManager.main_worktree_root(d)
            return main_root if main_root is not None else d

    return cwd


def _assembler(project_dir: Path | None = None):
    with catch_broken_install():
        from ..assembler import Assembler

    return Assembler(project_dir or _project_dir())


def _guard_not_inside_linked_worktree() -> None:
    """Refuse lifecycle ops issued from inside a linked worktree, except those
    designed to be run there (`wt exec`, `wt env`).

    HATS-788: checks the **raw `Path.cwd()`**, NOT a passed `project_dir`.
    Callers used to hand this `_project_dir()`, which has already HOPPED to the
    main checkout (HATS-524), so `is_inside_linked_worktree` inspected MAIN and
    the guard silently no-op'd from inside a worktree — letting a
    teardown command (`wt merge`/`discard`, `task transition done`) run
    `git worktree remove --force` on the operator's own cwd. Resolving cwd
    here, once, also keeps the check uncopyable-wrong at the call sites.

    `Path.cwd()` is safe here: the guard runs *before* any teardown, while the
    worktree (and thus cwd) still exists.

    Originally inline in ``wt_create`` (HATS-060); lifted to a helper so
    ``wt_merge`` / ``wt_discard`` / ``wt_list`` / ``task transition`` share it.

    Prints a guidance message and ``sys.exit(1)`` on breach. Returns None
    when CWD is OK (main worktree or non-git path).
    """
    with catch_broken_install():
        from ai_hats_wt import WorktreeManager

    cwd = Path.cwd()
    if WorktreeManager.is_inside_linked_worktree(cwd):
        console.print("[red]Cannot run this command from inside a linked worktree[/]")
        console.print(f"  You are in: {cwd}")
        main_root = WorktreeManager.main_worktree_root(cwd)
        if main_root is not None:
            console.print(f"  Run it from the main checkout: [bold]cd {main_root}[/]")
        else:
            console.print("  Run from the main repo.")
        console.print("  To act on the active worktree without leaving it, use")
        console.print("  [bold]ai-hats wt exec[/] / [bold]ai-hats wt env[/].")
        sys.exit(1)


def _task_manager(project_dir: Path | None = None):
    """Construct a TaskManager with the project's configured task-id prefix.

    Falls back to auto-detection (and persists the result) when the project
    has existing task folders but no `task_prefix` in ai-hats.yaml — keeps
    legacy repos on their historical prefix without manual migration.
    """
    with catch_broken_install():
        from ..models import ProjectConfig
        from ai_hats_tracker.state import TaskManager
        from ..tracker_wiring import tracker_paths
        from ..wt_effects import WtWorktreeEffects

    pdir = project_dir or _project_dir()
    config_path = pdir / PROJECT_CONFIG
    prefix = ProjectConfig.resolve_task_prefix(pdir, config_path)
    # HATS-866/864: the CLI is the integrator chokepoint binding the FSM's
    # needs_worktree effect to the wt engine and the layout to TrackerPaths.
    return TaskManager(
        pdir,
        prefix=prefix,
        layout=tracker_paths(pdir),
        worktree_effects=WtWorktreeEffects(pdir),
    )
