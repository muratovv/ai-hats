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

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..composition_seam import MissingProviderError, RoleNotFoundError
    from ..libraries.models import CheckBindingError
    from ..paths import NotAnAiHatsProjectError
    from ..surface_registry import UnknownSurfaceError
    from ..role_spec import RoleSpecError

console = Console()
logger = logging.getLogger(__name__)


def _handle_role_not_found(exc: "RoleNotFoundError") -> NoReturn:
    """Render a `RoleNotFoundError` as a friendly stderr message and exit 2.

    Single source of truth for the unknown-role UX. Reached from the root
    group's dispatch (HATS-1228), so it now genuinely covers every surface that
    composes — including ``ai-hats reflect *``, which this docstring claimed
    since HATS-547 while ``reflect.py`` caught nothing and shipped a traceback.

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


def _handle_unknown_provider(exc: "UnknownSurfaceError") -> NoReturn:
    """Render an ``UnknownSurfaceError`` as friendly stderr + exit 2.

    The provider analogue of ``_handle_role_not_found`` for the bare-launch
    surface (HATS-965): names the bad provider, lists registered ones, hints at
    ``ai-hats list providers``. No ``Traceback`` reaches the user. Output
    contract asserted by ``tests/e2e/test_unknown_provider_friendly_error.py``.

    No install instruction: ai-hats does not install surfaces (HATS-1826), so the
    only actionable fact is which names do resolve.
    """
    click.echo(f"Error: Provider {exc.name!r} not found.\n", err=True)
    click.echo("Available providers:", err=True)
    for name in exc.available:
        click.echo(f"  - {name}", err=True)
    click.echo("\nHint: 'ai-hats list providers' shows the full table.", err=True)
    sys.exit(2)


def _handle_missing_provider(exc: "MissingProviderError") -> NoReturn:
    """Render a ``MissingProviderError`` as friendly stderr + exit 2.

    The absent-provider analogue of ``_handle_unknown_provider`` (HATS-1224):
    no name to echo back, so it leads with the remediation command. Output
    contract asserted by ``tests/e2e/test_missing_provider_friendly_error.py``.
    """
    click.echo("Error: no provider configured in ai-hats.yaml.\n", err=True)
    example = exc.available[0] if exc.available else "<provider>"
    click.echo(f"Fix: ai-hats config set -p {example}\n", err=True)
    click.echo("Available providers:", err=True)
    for name in exc.available:
        click.echo(f"  - {name}", err=True)
    click.echo("\nHint: 'ai-hats list providers' shows the full table.", err=True)
    sys.exit(2)


def _handle_no_project(exc: Exception) -> NoReturn:
    """Render a ``ProjectNotFoundError`` as a friendly message + exit 2.

    The resolver no longer falls back to cwd (the stray-ancestor bug), so an
    un-onboarded directory is an ANSWER — point at init instead of a traceback.
    """
    console.print(f"[red]Error[/]: {exc}")
    console.print("cd to your project root, or run [bold]ai-hats init[/] to onboard this one.")
    raise SystemExit(2)


def _handle_not_a_project(exc: "NotAnAiHatsProjectError") -> NoReturn:
    """Render a ``NotAnAiHatsProjectError`` as a friendly message + exit 2.

    Lifted verbatim out of ``main_entry`` (HATS-839) so it can join the registry
    below; ``main_entry`` still owns the pre-click phase and routes through it.
    """
    console.print(f"[red]Error:[/] {exc}")
    sys.exit(2)


def _handle_role_spec_error(exc: "RoleSpecError") -> NoReturn:
    click.echo(f"Error: {exc}", err=True)
    sys.exit(2)


def _handle_check_binding_error(exc: "CheckBindingError") -> NoReturn:
    """A binding that cannot be installed refuses in words, not in a stack.

    The message already carries every fact the composer had (declaring
    component, skill/script, the reason) — what was missing was a renderer:
    composing raises this from ``resolve_checks``, and HATS-1541 measured 63
    lines of traceback and exit 1 on ``ai-hats --dry-run``.
    """
    click.echo(f"Error: {exc}", err=True)
    sys.exit(2)


def _friendly_error_handlers() -> "tuple[tuple[type[Exception], Callable[..., NoReturn]], ...]":
    """The typed errors the CLI renders instead of a traceback, most-specific first.

    Imported lazily: this module loads on every CLI path, the seam does not.
    """
    with catch_broken_install():
        from ..composition_seam import MissingProviderError, RoleNotFoundError
        from ..libraries.models import CheckBindingError, ComponentKeyError
        from ai_hats_core.layout import ProjectNotFoundError

        from ..paths import NotAnAiHatsProjectError
        from ..surface_registry import UnknownSurfaceError
        from ..role_spec import RoleSpecError

    return (
        (RoleSpecError, _handle_role_spec_error),
        (RoleNotFoundError, _handle_role_not_found),
        (UnknownSurfaceError, _handle_unknown_provider),
        (MissingProviderError, _handle_missing_provider),
        (NotAnAiHatsProjectError, _handle_not_a_project),
        (ProjectNotFoundError, _handle_no_project),
        (CheckBindingError, _handle_check_binding_error),
        # HATS-1545 F7: a key defect is the same class of message as a binding
        # defect — both are a declared gate that cannot install, and a traceback
        # is what HATS-1541 measured and removed for the sibling type.
        (ComponentKeyError, _handle_check_binding_error),
    )


def dispatch_friendly_error(exc: BaseException) -> bool:
    """Render ``exc`` through its registered handler (which exits 2), or return False.

    The single opt-out-proof rendering point (HATS-1228): surfaces used to catch
    these per call site, so one that forgot — ``cli/reflect.py``, five compose
    sites and no arms — shipped a traceback. Matching is by ``isinstance``:
    ``MissingProviderError`` is itself a ``RuntimeError`` subclass.
    """
    for exc_type, handler in _friendly_error_handlers():
        if isinstance(exc, exc_type):
            handler(exc)
    return False


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
    os.execvp(claude_bin, [claude_bin, prompt])  # noqa: S606


class DeadCwdError(click.ClickException):
    """The current working directory no longer exists (HATS-788).

    Commonly: the linked worktree you were standing in was just torn down by
    `rack transition <id> done` / `wt merge`. Resolving the project root from a
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


def broken_install_notice(exc: Exception) -> str:
    """The one broken-install notice, rendered for THIS install shape (HATS-1368)."""
    from .._bootstrap import repair_command

    return (
        f"Inconsistent or broken ai-hats installation ({exc}).\n"
        "Likely cause: package files are out of sync or corrupted.\n"
        f"Repair command: {repair_command()}\n"
        "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v"
    )


class InconsistentInstallError(click.ClickException):
    """Raised when an internal import fails due to a broken or inconsistent install (HATS-1120)."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(broken_install_notice(exc))

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


def _assembler(project_dir: Path):
    """The caller resolves the project; this helper only survives the import guard."""
    with catch_broken_install():
        from ..assembler import Assembler

    return Assembler(project_dir)


def _guard_not_inside_linked_worktree() -> None:
    """Refuse lifecycle ops issued from inside a linked worktree, except those
    designed to be run there (`wt exec`, `wt env`).

    HATS-788: checks the **raw `Path.cwd()`**, NOT a passed `project_dir`.
    Callers used to hand this `_project_dir()`, which has already HOPPED to the
    main checkout (HATS-524), so `is_inside_linked_worktree` inspected MAIN and
    the guard silently no-op'd from inside a worktree — letting a
    teardown command (`wt merge`/`discard`, `rack transition <id> done`) run
    `git worktree remove --force` on the operator's own cwd. Resolving cwd
    here, once, also keeps the check uncopyable-wrong at the call sites.

    `Path.cwd()` is safe here: the guard runs *before* any teardown, while the
    worktree (and thus cwd) still exists.

    Originally inline in ``wt_create`` (HATS-060); lifted to a helper so
    ``wt_merge`` / ``wt_discard`` / ``wt_list`` / ``rack transition`` share it.

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
