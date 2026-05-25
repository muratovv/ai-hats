"""`ai-hats wt` — manage git worktrees for isolated work."""

from __future__ import annotations

import os
import re
import subprocess
import sys

import click

from ._helpers import _guard_not_inside_linked_worktree, _project_dir, console


# HATS-482 (B-07): branch-name input filter for `wt create`. Permissive on
# case (mixed case is now safe with case-preserving `_state_key`), strict
# on chars that break path math, git itself, or state-file naming:
#   * leading dot/dash/slash → git refuses anyway, fail earlier with hint;
#   * whitespace → state file name corruption + shell injection footgun;
#   * `..` segment → would let an operator escape `worktrees_dir` if it
#     ever leaked into a path join. Cheap defense in depth.
_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]*$")


def _validate_branch_name(_ctx, _param, value: str) -> str:
    """Click callback: reject branch names that break paths or git."""
    if not _BRANCH_NAME_RE.match(value) or ".." in value:
        raise click.BadParameter(
            f"Invalid branch name '{value}'. "
            "Use [A-Za-z0-9/_.-], no leading dot/dash/slash, "
            "no whitespace, no '..' segment."
        )
    return value


def _resolve_worktree(branch: str | None = None):
    """Resolve a WorktreeManager from branch arg, CWD, or sole active worktree.

    Returns None when nothing can be found.

    HATS-482 (R-08): when no branch is supplied AND CWD is not in a linked
    worktree, refuse to silently grab ``list_active()[0]`` when ``>1``
    worktree is tracked — raises :class:`click.UsageError` listing branches
    so the operator explicitly disambiguates.  ``len(active) == 1`` keeps
    the prior convenience (no need to type the branch in the single-wt
    case).
    """
    import subprocess as _sp

    from ..worktree import WorktreeManager

    project_dir = _project_dir()

    if branch is not None:
        return WorktreeManager.load_for_branch(project_dir, branch)

    # CWD is inside a linked worktree → detect branch automatically.
    if WorktreeManager.is_inside_linked_worktree(project_dir):
        try:
            head = _sp.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except _sp.CalledProcessError:
            return None
        return WorktreeManager.load_for_branch(project_dir, head)

    # HATS-482 / R-08: fail-on-ambiguity instead of silent first-active.
    active = WorktreeManager.list_active(project_dir)
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    branches = ", ".join(m.branch_name for m in active)
    raise click.UsageError(
        f"Multiple active worktrees, specify which one as the first arg: {branches}"
    )


@click.group()
def wt():
    """Manage git worktrees for isolated work."""
    pass


@wt.command("create")
@click.argument("branch", callback=_validate_branch_name)
def wt_create(branch: str):
    """Create an isolated worktree on a new branch."""
    from ..worktree import (
        WorktreeCreateError,
        WorktreeLockError,
        WorktreeManager,
    )

    project_dir = _project_dir()

    # HATS-060: refuse to create from inside a linked worktree
    # (helper-extracted in HATS-482 / B-08 so merge/discard/list share it).
    _guard_not_inside_linked_worktree(project_dir)

    # HATS-479: the previous pre-check (load_for_branch outside any lock) was
    # the TOCTOU surface — two concurrent `wt create <same-branch>` callers
    # both saw `existing is None`, then both ran `git worktree add -b`, and
    # the loser got an opaque CalledProcessError + a leaked tempdir.
    # WorktreeManager.create() now re-checks under the repo-scoped L1 lock
    # and raises WorktreeCreateError with a friendly message; we just relay.
    mgr = WorktreeManager(project_dir, branch_name=branch)
    try:
        wt_path = mgr.create()
    except WorktreeCreateError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)
    except WorktreeLockError as exc:
        console.print("[red]wt create lock unavailable[/]")
        console.print(str(exc))
        sys.exit(1)

    mgr.save_state()
    console.print(f"[green]Worktree created[/]: {branch}")
    console.print(f"  Path: {wt_path}")
    console.print(f"  [dim]cd {wt_path}[/]")


@wt.command("merge")
@click.argument("branch", required=False)
@click.option("--squash", is_flag=True, default=False, help="Squash all commits into one")
@click.option("--force", is_flag=True, default=False, help="Merge even with uncommitted changes")
@click.option(
    "--accept-drift",
    is_flag=True,
    default=False,
    help="Proceed even if the base branch moved since worktree create (HATS-457)",
)
def wt_merge(branch: str | None, squash: bool, force: bool, accept_drift: bool):
    """Merge worktree changes back and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Default: --no-ff merge preserving commit history.
    Refuses if worktree has uncommitted changes (use --force to override).
    Refuses if the base branch moved since `wt create` — local or remote
    drift (use --accept-drift to override after re-verifying).
    """
    from ..worktree import (
        WorktreeDirtyError,
        WorktreeDriftError,
        WorktreePartialCleanupError,
    )

    # HATS-482 / B-08: guard before resolving CWD/_project_dir.
    _guard_not_inside_linked_worktree(_project_dir())

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt merge <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.merge(squash=squash, force=force, accept_drift=accept_drift)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    except WorktreeDriftError as e:
        # Drift message embeds filenames from the diverged commits — escape
        # so a filename like `[red]boom[/]` cannot inject Rich markup into
        # the operator's terminal.
        from rich.markup import escape as _escape
        console.print(f"[red]Refused (drift)[/]:\n{_escape(str(e))}")
        sys.exit(1)
    except WorktreePartialCleanupError as e:
        # HATS-482 / B-02: merge committed, worktree dir gone, but branch
        # cleanup failed for a known cause. State JSON intact so the
        # operator can retry after fixing the cause.
        console.print(
            f"[yellow]Worktree torn down, but branch '{e.branch_name}' "
            f"preserved[/] ({e.reason})"
        )
        console.print(f"  git: {e.stderr_tail}")
        console.print(
            f"  Manual cleanup: [bold]git branch -D {e.branch_name}[/] "
            f"(after resolving the cause)"
        )
        sys.exit(2)
    console.print(f"[green]Merged[/]: {name}")


@wt.command("discard")
@click.argument("branch", required=False)
@click.option("--force", is_flag=True, default=False, help="Discard even with uncommitted changes")
def wt_discard(branch: str | None, force: bool):
    """Discard worktree changes and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Refuses if worktree has uncommitted changes (use --force to override).
    """
    from ..worktree import WorktreeDirtyError, WorktreePartialCleanupError

    # HATS-482 / B-08: guard before resolving CWD/_project_dir.
    _guard_not_inside_linked_worktree(_project_dir())

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt discard <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.discard(force=force)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    except WorktreePartialCleanupError as e:
        # HATS-482 / B-02: worktree dir gone, branch survived.
        console.print(
            f"[yellow]Worktree torn down, but branch '{e.branch_name}' "
            f"preserved[/] ({e.reason})"
        )
        console.print(f"  git: {e.stderr_tail}")
        console.print(
            f"  Manual cleanup: [bold]git branch -D {e.branch_name}[/] "
            f"(after resolving the cause)"
        )
        sys.exit(2)
    console.print(f"[green]Discarded[/]: {name}")


@wt.command("list")
def wt_list():
    """List all git worktrees."""
    from ..worktree import WorktreeManager

    project_dir = _project_dir()
    # HATS-482 / B-08: guard CWD-from-inside-linked-worktree.
    _guard_not_inside_linked_worktree(project_dir)
    worktrees = WorktreeManager.list_worktrees(project_dir)
    tracked_branches = {m.branch_name for m in WorktreeManager.list_active(project_dir)}

    if not worktrees:
        console.print("[dim]No worktrees[/]")
        return

    for w in worktrees:
        branch = w.get("branch", "?")
        path = w.get("path", "?")
        marker = " [green]← tracked[/]" if branch in tracked_branches else ""
        console.print(f"  {branch}: {path}{marker}")


@wt.command("status")
def wt_status():
    """Show all tracked worktrees."""
    from ..worktree import WorktreeManager

    active = WorktreeManager.list_active(_project_dir())
    if not active:
        console.print("[dim]No active worktrees[/]")
        return

    for mgr in active:
        console.print(f"  Branch: [bold]{mgr.branch_name}[/]  Path: {mgr.worktree_path}")


@wt.command("exec", context_settings={"ignore_unknown_options": True})
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED, required=True)
def wt_exec(cmd_args: tuple[str, ...]):
    """Run a command inside the active worktree (cwd + PYTHONPATH=src).

    Replaces gnarly shell boilerplate like:

    \b
        WT=/var/folders/.../ai-hats-wt-...
        PYTHONPATH=$WT/src python -m pytest tests/test_foo.py -xvs

    Use `--` to separate ai-hats args from the inner command:

    \b
        ai-hats wt exec -- pytest tests/test_foo.py -xvs
        ai-hats wt exec -- python -c 'import ai_hats; print(ai_hats.__file__)'
        ai-hats wt exec -- ruff check src/
    """
    mgr = _resolve_worktree()
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        console.print("[red]Active worktree has no path[/]")
        sys.exit(1)

    env = os.environ.copy()
    src_path = str(wt_path / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path

    try:
        result = subprocess.run(list(cmd_args), cwd=str(wt_path), env=env)
    except FileNotFoundError as e:
        console.print(f"[red]Command not found:[/] {e.filename}")
        sys.exit(127)
    sys.exit(result.returncode)


@wt.command("env")
def wt_env():
    """Print shell exports for the active worktree (eval-friendly).

    Usage:

    \b
        eval "$(ai-hats wt env)"
        # now $WT and $PYTHONPATH are set; cd to it manually if needed
    """
    mgr = _resolve_worktree()
    if mgr is None:
        click.echo("# no active worktree", err=True)
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        click.echo("# active worktree has no path", err=True)
        sys.exit(1)

    click.echo(f'export WT="{wt_path}"')
    click.echo(f'export PYTHONPATH="{wt_path}/src${{PYTHONPATH:+:$PYTHONPATH}}"')
