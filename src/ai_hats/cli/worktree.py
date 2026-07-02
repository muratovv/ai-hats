"""`ai-hats wt` — manage git worktrees for isolated work."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import click

from ..paths import worktrees_dir  # ADR-0013 D4: state-dir base for the wt core
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

    from ai_hats_wt import WorktreeManager
    from ..wt_lifecycle import HOOK_LIFECYCLE

    project_dir = _project_dir()

    # ADR-0013 D3: every manager this resolver hands back may be torn down
    # (merge/discard), so it carries ai-hats's hook-running bundle.
    if branch is not None:
        return WorktreeManager.load_for_branch(
            project_dir,
            branch,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=worktrees_dir(project_dir),
        )

    # CWD is inside a linked worktree → detect branch automatically.
    # HATS-788: detect on the RAW cwd — `project_dir` has hopped to MAIN
    # (HATS-524) so `is_inside_linked_worktree(project_dir)` is always False
    # from inside a worktree. In practice the lifecycle guard refuses
    # merge/discard from inside a worktree before this runs; the raw-cwd check
    # keeps it correct for any unguarded caller. The tracker lookup
    # (`load_for_branch`) still uses the main `project_dir`.
    cwd = Path.cwd()
    if WorktreeManager.is_inside_linked_worktree(cwd):
        try:
            head = _sp.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except _sp.CalledProcessError:
            return None
        return WorktreeManager.load_for_branch(
            project_dir,
            head,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=worktrees_dir(project_dir),
        )

    # HATS-482 / R-08: fail-on-ambiguity instead of silent first-active.
    active = WorktreeManager.list_active(
        project_dir, lifecycle=HOOK_LIFECYCLE, state_dir=worktrees_dir(project_dir)
    )
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
    from ai_hats_wt import (
        WorktreeBaseBranchError,
        WorktreeCreateError,
        WorktreeLockError,
        WorktreeManager,
        assert_head_is_canonical_base,
    )

    project_dir = _project_dir()

    # HATS-060: refuse to create from inside a linked worktree
    # (helper-extracted in HATS-482 / B-08 so merge/discard/list share it).
    _guard_not_inside_linked_worktree()

    # HATS-518: refuse if main-repo HEAD is not on a canonical base branch.
    # Otherwise the worktree captures the feature branch as its merge target
    # and `wt merge` silently lands on the feature branch, not master.
    try:
        assert_head_is_canonical_base(project_dir)
    except WorktreeBaseBranchError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)

    # HATS-479: the previous pre-check (load_for_branch outside any lock) was
    # the TOCTOU surface — two concurrent `wt create <same-branch>` callers
    # both saw `existing is None`, then both ran `git worktree add -b`, and
    # the loser got an opaque CalledProcessError + a leaked tempdir.
    # WorktreeManager.create() now re-checks under the repo-scoped L1 lock
    # and raises WorktreeCreateError with a friendly message; we just relay.
    # HATS-823: thread the project's effective-role worktree carry (wt_in/wt_out
    # hooks) in at create; persisted to state for teardown (D3).
    from ..wt_carry import collect_carry_for_role
    from ..wt_lifecycle import HOOK_LIFECYCLE

    mgr = WorktreeManager(
        project_dir,
        branch_name=branch,
        lifecycle=HOOK_LIFECYCLE,
        state_dir=worktrees_dir(project_dir),
    )
    try:
        wt_path = mgr.create(wt_hooks=collect_carry_for_role(project_dir))
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
@click.option(
    "--skip-hooks",
    is_flag=True,
    default=False,
    help="Force teardown even if a wt_out hook fails — accepts losing "
    "unharvested gitignored data (HATS-823).",
)
def wt_merge(
    branch: str | None,
    squash: bool,
    force: bool,
    accept_drift: bool,
    skip_hooks: bool,
):
    """Merge worktree changes back and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Default: --no-ff merge preserving commit history.
    Refuses if worktree has uncommitted changes (use --force to override).
    Refuses if the base branch moved since `wt create` — local or remote
    drift (use --accept-drift to override after re-verifying).
    """
    from ai_hats_wt import (
        WorktreeBaseBranchMismatchError,  # HATS-533
        WorktreeDirtyError,
        WorktreeDriftError,
        WorktreeMainRepoMidMergeError,  # HATS-587 / F4
        WorktreePartialCleanupError,
        WorktreeRemoveError,
        WorktreeStateIncompleteError,  # HATS-714
        WorktreeTeardownAborted,  # HATS-823 / ADR-0013 D8
    )

    # HATS-482 / B-08: guard before resolving CWD/_project_dir.
    _guard_not_inside_linked_worktree()

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt merge <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.merge(
            squash=squash,
            force=force,
            accept_drift=accept_drift,
            skip_hooks=skip_hooks,
        )
    except WorktreeTeardownAborted as e:
        # HATS-823 / ADR-0013 D8: a wt_out hook failed; teardown aborted
        # fail-closed, the worktree + gitignored data are preserved. The hook
        # detail (recovery + --skip-hooks escape) rides as the __cause__; fall
        # back to the abort itself for a future causeless (non-hook) veto.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (wt_out hook failed)[/]: {_escape(str(e.__cause__ or e))}")
        sys.exit(1)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    except WorktreeStateIncompleteError as e:
        # HATS-714: the state file is present but lacks `original_branch`
        # (corrupt / hand-edited / legacy). Surface the typed refusal instead
        # of the pre-714 `git rev-parse None` traceback. The message already
        # carries the recovery recipe; escape defensively (branch names can
        # in principle contain Rich-markup characters).
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (incomplete worktree state)[/]: {_escape(str(e))}")
        sys.exit(1)
    except WorktreeBaseBranchMismatchError as e:
        # HATS-533: main-repo HEAD wandered off `_original_branch` between
        # `wt create` and `wt merge`. The merge would otherwise silently
        # land on `e.current` instead of `e.expected` — same wrong-branch
        # class as HATS-486. Refuse before any mutation; surface a
        # copy-pasteable recipe naming the right branch and the main-repo
        # path. Escape current/expected defensively (branch names can in
        # principle contain Rich-markup characters).
        from rich.markup import escape as _escape

        project_dir = _project_dir()
        console.print(f"[red]Refused (base branch mismatch)[/]: {_escape(str(e))}")
        console.print("Resolve:")
        console.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        console.print(
            f"  [cyan]git checkout {_escape(e.expected)}[/]",
            soft_wrap=True,
        )
        console.print("  [cyan]ai-hats wt merge[/]", soft_wrap=True)
        sys.exit(1)
    except WorktreeMainRepoMidMergeError as e:
        # HATS-587 / F4: main repo already mid-merge (foreign MERGE_HEAD).
        # Refuse cleanly with the resolve recipe — no traceback. Worktree
        # and branch are untouched (guard runs before any mutation), so
        # the operator can clean up the main repo and re-run unchanged.
        from rich.markup import escape as _escape

        project_dir = _project_dir()
        console.print(f"[red]Refused (main repo mid-merge)[/]: {_escape(str(e))}")
        console.print("Resolve the in-progress merge first:")
        console.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        console.print(
            "  [cyan]git merge --abort[/]  [dim]# or resolve conflicts + git commit[/]",
            soft_wrap=True,
        )
        console.print("  [cyan]ai-hats wt merge[/]", soft_wrap=True)
        sys.exit(1)
    except WorktreeDriftError as e:
        # Drift message embeds filenames from the diverged commits — escape
        # so a filename like `[red]boom[/]` cannot inject Rich markup into
        # the operator's terminal.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (drift)[/]:\n{_escape(str(e))}")
        # HATS-509: the recipe (full command form) lives here, not in the
        # exception body, so the sibling `task transition done` handler
        # can name its own surface without inheriting a misleading
        # `--accept-drift` hint that points at the wrong command.
        console.print(
            "Re-verify your changes against the new base, "
            "then re-run with [cyan]ai-hats wt merge --accept-drift[/]."
        )
        sys.exit(1)
    except WorktreeRemoveError as e:
        # HATS-488 / B-03: merge committed, but worktree dir cleanup
        # failed for a non-junk reason (held-open files, perms). Branch
        # state JSON intact; operator can investigate and retry.
        from rich.markup import escape as _escape

        console.print(f"[yellow]Merged, but worktree dir still on disk[/]: {_escape(str(e.path))}")
        console.print(f"  git: {_escape(e.stderr_tail)}")
        console.print(
            f"  Manual cleanup: investigate the cause "
            f"(e.g. lsof '{_escape(str(e.path))}'), then [bold]rm -rf "
            f"{_escape(str(e.path))}[/] + [bold]git worktree prune[/]."
        )
        sys.exit(2)
    except WorktreePartialCleanupError as e:
        # HATS-482 / B-02: merge committed, worktree dir gone, but branch
        # cleanup failed for a known cause. State JSON intact so the
        # operator can retry after fixing the cause.
        # branch_name + stderr_tail come from git output (untrusted re:
        # Rich markup); escape before console.print mirrors the drift
        # handler above.
        from rich.markup import escape as _escape

        console.print(
            f"[yellow]Worktree torn down, but branch '{_escape(e.branch_name)}' "
            f"preserved[/] ({e.reason})"
        )
        console.print(f"  git: {_escape(e.stderr_tail)}")
        console.print(
            f"  Manual cleanup: [bold]git branch -D {_escape(e.branch_name)}[/] "
            f"(after resolving the cause)"
        )
        sys.exit(2)
    console.print(f"[green]Merged[/]: {name}")


@wt.command("discard")
@click.argument("branch", required=False)
@click.option("--force", is_flag=True, default=False, help="Discard even with uncommitted changes")
@click.option(
    "--force-remove",
    is_flag=True,
    default=False,
    help="If `git worktree remove --force` fails (e.g. held-open files), "
    "fall back to rm -rf. HATS-488 / B-03: opt-in only — default refuses "
    "to silently nuke residual data.",
)
@click.option(
    "--skip-hooks",
    is_flag=True,
    default=False,
    help="Force teardown even if a wt_out hook fails — accepts losing "
    "unharvested gitignored data (HATS-823).",
)
def wt_discard(branch: str | None, force: bool, force_remove: bool, skip_hooks: bool):
    """Discard worktree changes and clean up.

    Without BRANCH: auto-detect from CWD (if inside a linked worktree).
    Refuses if worktree has uncommitted changes (use --force to override).
    Refuses if `git worktree remove --force` cannot delete the directory
    (e.g. held-open files) — pass --force-remove to fall back to rm -rf.
    """
    from ai_hats_wt import (
        WorktreeDirtyError,
        WorktreePartialCleanupError,
        WorktreeRemoveError,
        WorktreeTeardownAborted,  # HATS-823 / ADR-0013 D8
    )

    # HATS-482 / B-08: guard before resolving CWD/_project_dir.
    _guard_not_inside_linked_worktree()

    mgr = _resolve_worktree(branch)
    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        if branch is None:
            console.print("  Specify a branch: [bold]ai-hats wt discard <branch>[/]")
        sys.exit(1)

    name = mgr.branch_name
    try:
        mgr.discard(force=force, force_remove=force_remove, skip_hooks=skip_hooks)
    except WorktreeTeardownAborted as e:
        # HATS-823 / ADR-0013 D8: `discard` is still fail-closed on a wt_out hook
        # — the data may matter even when the work doesn't. The hook detail
        # (--skip-hooks escape) rides as the __cause__; fall back to the abort
        # itself for a future causeless (non-hook) veto.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (wt_out hook failed)[/]: {_escape(str(e.__cause__ or e))}")
        sys.exit(1)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        sys.exit(1)
    except WorktreeRemoveError as e:
        # HATS-488 / B-03: data-preservation guard fired — git couldn't
        # delete the worktree dir and operator hasn't opted in to rm-rf.
        # Path + stderr_tail come from git output / fs error (untrusted
        # re: Rich markup); escape before console.print.
        from rich.markup import escape as _escape

        console.print(
            f"[yellow]Refused to remove worktree dir[/] (data preservation): {_escape(str(e.path))}"
        )
        console.print(f"  git: {_escape(e.stderr_tail)}")
        console.print(
            "  Re-run with [bold]--force-remove[/] if the dir contents are "
            "known to be junk, or clean it manually first."
        )
        sys.exit(2)
    except WorktreePartialCleanupError as e:
        # HATS-482 / B-02: worktree dir gone, branch survived.
        # branch_name + stderr_tail come from git output (untrusted re:
        # Rich markup); escape before console.print.
        from rich.markup import escape as _escape

        console.print(
            f"[yellow]Worktree torn down, but branch '{_escape(e.branch_name)}' "
            f"preserved[/] ({e.reason})"
        )
        console.print(f"  git: {_escape(e.stderr_tail)}")
        console.print(
            f"  Manual cleanup: [bold]git branch -D {_escape(e.branch_name)}[/] "
            f"(after resolving the cause)"
        )
        sys.exit(2)
    console.print(f"[green]Discarded[/]: {name}")


@wt.command("list")
def wt_list():
    """List all git worktrees."""
    from ai_hats_wt import WorktreeManager

    project_dir = _project_dir()
    # HATS-482 / B-08: guard CWD-from-inside-linked-worktree.
    _guard_not_inside_linked_worktree()
    worktrees = WorktreeManager.list_worktrees(project_dir)
    tracked_branches = {
        m.branch_name
        for m in WorktreeManager.list_active(project_dir, state_dir=worktrees_dir(project_dir))
    }

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
    from ai_hats_wt import WorktreeManager

    project_dir = _project_dir()
    active = WorktreeManager.list_active(project_dir, state_dir=worktrees_dir(project_dir))
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

    # HATS-887: strip GIT_* plumbing so `ai-hats wt exec -- git …` resolves from
    # the worktree (cwd), not an ambient GIT_DIR a merge/hook context exports.
    env = os.environ.copy()
    for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(_var, None)
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
