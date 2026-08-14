"""`ai-hats wt` — manage git worktrees for isolated work."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import click

from ai_hats_core import scrubbed_git_env
from ..paths import (  # ADR-0013 D4: path bases for the wt core
    worktree_checkouts_dir,
    worktrees_dir,
)
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
                env=scrubbed_git_env(),
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


def _peel_selector(args: list[str]) -> str | None:
    """Peel a leading worktree selector off ``args`` (in place); None if absent.

    A parser, not a resolver — the caller resolves, so an unresolvable selector
    refuses instead of falling back to cwd. ``args[0]`` is a selector only when
    it names an active worktree; otherwise it is the command (HATS-859).
    """
    from ai_hats_wt import WorktreeManager
    from ..wt_lifecycle import HOOK_LIFECYCLE

    if not args:
        return None
    project_dir = _project_dir()
    active = WorktreeManager.list_active(
        project_dir, lifecycle=HOOK_LIFECYCLE, state_dir=worktrees_dir(project_dir)
    )
    if not any(m.branch_name == args[0] for m in active):
        return None
    selector = args.pop(0)
    # Click strips only a *leading* `--`; one that trailed the selector can
    # survive in cmd_args — drop it so it never reaches the inner command.
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        raise click.UsageError("No command to run. Usage: ai-hats wt exec [<branch>] [--] <cmd…>")
    return selector


def _effective_dir(wt_path: Path, subdir: str | None = None) -> Path:
    """Where `wt exec` should run (HATS-1205 — an env wrapper, not a teleporter).

    ``-C`` wins; else the caller's cwd when it is inside this worktree; else the
    worktree root. Paths are resolved before comparison: on macOS a worktree
    minted under ``/var/folders`` reports a cwd under ``/private/var/folders``.
    """
    root = wt_path.resolve()
    if subdir is not None:
        target = (wt_path / subdir).resolve()
        if not target.is_relative_to(root):
            raise click.UsageError(f"--cd escapes the worktree: {subdir}")
        if not target.is_dir():
            raise click.UsageError(f"--cd target is not a directory in the worktree: {subdir}")
        return target
    try:
        cwd = Path.cwd().resolve()
    except OSError:  # cwd unlinked under us
        return wt_path
    return cwd if cwd.is_relative_to(root) else wt_path


def _owner_root(run_dir: Path, wt_path: Path) -> Path:
    """The checkout root whose environment `run_dir` belongs to (HATS-1205).

    Nearest ancestor carrying a ``pyproject.toml``, bounded by the worktree
    root — so a subproject gets its own ``src`` instead of the outer repo's
    packages, while a plain subdirectory keeps the worktree-root workspace.
    """
    root = wt_path.resolve()
    here = run_dir.resolve()
    for candidate in (here, *here.parents):
        if not candidate.is_relative_to(root):
            break
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return wt_path


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

    # HATS-942: resolve the configured base/merge-target (both None => today's
    # canonical behavior); fail loud on a configured-but-absent branch.
    from ..wt_config import WorktreeConfigError, resolve_worktree_branches

    try:
        base_branch, merge_target = resolve_worktree_branches(project_dir)
    except WorktreeConfigError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)

    # HATS-518/942: refuse if main-repo HEAD is not on the worktree merge target
    # (canonical set when unconfigured). Otherwise the worktree captures the
    # wrong merge target and `wt merge` silently lands on it.
    try:
        assert_head_is_canonical_base(project_dir, merge_target)
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
    from ..wt_effects import collect_carry_for_project
    from ..wt_lifecycle import HOOK_LIFECYCLE

    mgr = WorktreeManager(
        project_dir,
        branch_name=branch,
        base_branch=base_branch,
        merge_target=merge_target,
        lifecycle=HOOK_LIFECYCLE,
        state_dir=worktrees_dir(project_dir),
        worktree_checkouts_dir=worktree_checkouts_dir(project_dir),  # HATS-1632
    )
    try:
        wt_path = mgr.create(wt_hooks=collect_carry_for_project(project_dir))
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


def _print_blockers(blockers, *, note_unprobed_checks: bool = True) -> None:
    """Render what else would refuse this merge (HATS-1654) — rendering only.

    The guards fire one per run, so a merge can cost a run per fact. Saying what
    was NOT probed matters as much: an empty list must not read as "clear to
    merge" while a `wt:pre-merge` check is still ahead.
    """
    from rich.markup import escape as _escape

    for blocker in blockers:
        console.print(f"[yellow]Also blocking ({blocker.kind})[/]:")
        for line in blocker.message.splitlines():
            console.print(f"  {_escape(line)}", soft_wrap=True)
    if note_unprobed_checks:
        console.print("[dim]Not probed: wt:pre-merge checks — each runs its own command.[/]")


def _merge_ticket_accepted(branch: str) -> bool:
    """Did the supervisor answer the guard's question about THIS merge?

    Peek, never spend: a merge that then refuses for drift or a dirty tree must
    give the click back (HATS-1682).
    """
    from ai_hats_library.hooks import consent_ticket

    try:
        # ``None``: the branch may have been auto-detected here and merely typed
        # (or not) there, so the invocation is the binding that holds.
        return consent_ticket.peek(None, argv=sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 — an unreadable store is not consent
        console.print(f"[yellow]consent ticket unreadable[/]: {exc}")
        return False


@wt.command("merge")
@click.argument("branch", required=False)
@click.option("--squash", is_flag=True, default=False, help="Squash all commits into one")
@click.option("--force", is_flag=True, default=False, help="Merge even with uncommitted changes")
@click.option(
    "--accept-drift",
    is_flag=True,
    default=False,
    help="Proceed even if the base branch moved since worktree create",
)
@click.option(
    "--skip-hooks",
    is_flag=True,
    default=False,
    help="Force teardown even if a wt_out hook fails — accepts losing unharvested gitignored data.",
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
        Blocker,  # HATS-1654
        WorktreeBaseBranchMismatchError,  # HATS-533
        WorktreeDirtyError,
        WorktreeDriftError,
        WorktreeMainRepoMidMergeError,  # HATS-587 / F4
        WorktreeMergeConflictError,  # HATS-1651
        WorktreeMergeConsentError,  # HATS-1019
        WorktreeMergeLeftoverError,  # HATS-1651
        WorktreePartialCleanupError,
        WorktreeRebasedBranchError,  # HATS-1370
        WorktreeRemoveError,
        WorktreeMergeAborted,  # HATS-1540 / ADR-0019
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

    def other_blockers(raised: str) -> list:
        """Every blocker but the one that already raised — a failed probe is a row, not silence."""
        try:
            found = mgr.probe_blockers(force=force, accept_drift=accept_drift)
        except Exception as exc:  # noqa: BLE001 — a broken probe must not eat the refusal
            return [Blocker("probe", f"could not check the other blockers: {exc}")]
        return [b for b in found if b.kind != raised]

    try:
        mgr.merge(
            squash=squash,
            force=force,
            accept_drift=accept_drift,
            skip_hooks=skip_hooks,
            # HATS-1682: the guard asks about THIS command where the role
            # declared `wt: [pre-merge]`, and the ticket it minted is the answer.
            consent=_merge_ticket_accepted(name),
        )
    except WorktreeTeardownAborted as e:
        # HATS-823 / ADR-0013 D8: a wt_out hook failed; teardown aborted
        # fail-closed, the worktree + gitignored data are preserved. The hook
        # detail (recovery + --skip-hooks escape) rides as the __cause__; fall
        # back to the abort itself for a future causeless (non-hook) veto.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (wt_out hook failed)[/]: {_escape(str(e.__cause__ or e))}")
        sys.exit(1)
    except WorktreeMergeAborted as e:
        # HATS-1540: a `wt:pre-merge` check refused. Nothing was merged and the
        # worktree is intact, so the recipe is the check's own words — it names
        # the command that clears it (ADR-0019: a refusal is an action).
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (checks)[/]: {_escape(str(e))}")
        _print_blockers(other_blockers("checks"), note_unprobed_checks=False)
        sys.exit(1)
    except WorktreeMergeConsentError as e:
        # HATS-1019: recipe lives here (HATS-509 split) — the deny doubles
        # as the review-handoff directive.
        console.print(f"[red]Refused (review consent required)[/]: {e}")
        console.print(
            "This task is ready for review — STOP and hand it off to the "
            "supervisor. Consent is the supervisor's to give, from the environment "
            "that launched this session — an inline prefix on the agent's own "
            "command is refused as a self-grant (HATS-1639). Once review passes "
            "(supervisor saw the diff, notes resolved, explicit go) — one line, "
            "a lone export dies with the shell that ran it (HATS-1654):"
        )
        console.print(
            f"  [cyan]export AI_HATS_MERGE_ACK=1 && ai-hats wt merge {name}[/]",
            soft_wrap=True,
        )
        _print_blockers(other_blockers("consent"))
        sys.exit(1)
    except WorktreeDirtyError as e:
        console.print(f"[red]Refused[/]: {e}")
        _print_blockers(other_blockers("dirty"))
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
        _print_blockers(other_blockers("base-mismatch"))
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
    except WorktreeMergeConflictError as e:
        # HATS-1651: the rollback was verified before this was raised, so the
        # recipe can send the operator straight at the conflict. Resolve on the
        # BRANCH, not in main: a hand-merge in the main checkout produces a merge
        # commit outside the worktree bookkeeping this command owns.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (merge conflict)[/]: {_escape(str(e))}")
        console.print("Resolve on the branch, then re-run this command:")
        console.print(f"  [cyan]cd {mgr.worktree_path}[/]", soft_wrap=True)
        console.print(f"  [cyan]git merge {e.base_branch}[/]  [dim]# resolve there, commit[/]")
        console.print(f"  [cyan]ai-hats wt merge {name}[/]", soft_wrap=True)
        sys.exit(1)
    except WorktreeMergeLeftoverError as e:
        # HATS-1651: the rollback did NOT restore the main checkout. Nothing is
        # claimed about it here beyond what was observed, and the cleanup is the
        # operator's call — this command will not guess with `reset --hard` over
        # a checkout that may hold their uncommitted work.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (merge left state behind)[/]: {_escape(str(e))}")
        console.print("Inspect and clean up the main checkout by hand before retrying:")
        console.print(f"  [cyan]cd {_project_dir()}[/]", soft_wrap=True)
        console.print("  [cyan]git status[/]", soft_wrap=True)
        sys.exit(1)
    except WorktreeRebasedBranchError as e:
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (rebased branch)[/]: {_escape(str(e))}")
        console.print(
            "To confirm cleanup of this rebased worktree branch without re-merging, re-run with "
            "[cyan]--accept-drift[/]."
        )
        _print_blockers(other_blockers("rebased"))
        sys.exit(1)
    except WorktreeDriftError as e:
        # Drift message embeds filenames from the diverged commits — escape
        # so a filename like `[red]boom[/]` cannot inject Rich markup into
        # the operator's terminal.
        from rich.markup import escape as _escape

        console.print(f"[red]Refused (drift)[/]:\n{_escape(str(e))}")
        # HATS-509: the recipe (full command form) lives here, not in the
        # exception body, so the sibling `rack transition done` handler
        # can name its own surface without inheriting a misleading
        # `--accept-drift` hint that points at the wrong command.
        base = _escape(e.base_branch or "<base>")
        if e.worktree_path:
            console.print(f"  [cyan]cd {_escape(str(e.worktree_path))}[/]", soft_wrap=True)
        console.print(f"  [cyan]git rebase {base}[/]", soft_wrap=True)
        # HATS-1307: the rebase IS the fix — drift is containment, so a rebased
        # branch passes the guard. --accept-drift stays for a stale baseline
        # the operator merges knowingly.
        console.print(
            "Re-verify against the new base, then re-run this merge. "
            "To merge the stale baseline on purpose instead, re-run with "
            "[cyan]--accept-drift[/]."
        )
        _print_blockers(other_blockers("drift"))
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
    "fall back to rm -rf. Opt-in only — default refuses "
    "to silently nuke residual data.",
)
@click.option(
    "--skip-hooks",
    is_flag=True,
    default=False,
    help="Force teardown even if a wt_out hook fails — accepts losing unharvested gitignored data.",
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
@click.option(
    "-C",
    "--cd",
    "subdir",
    default=None,
    metavar="<subdir>",
    help="Worktree-relative directory to run in (default: your cwd when it is "
    "inside the worktree, else the worktree root).",
)
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED, required=True)
def wt_exec(subdir: str | None, cmd_args: tuple[str, ...]):
    """Run a command in a worktree, where you stand (cwd + workspace PYTHONPATH).

    A leading token matching an active branch is the selector and always wins;
    without one the worktree is inferred from cwd, else the sole active one. `--`
    optional — but required when the inner command has its own `-C`:

    \b
        ai-hats wt exec -- pytest tests/test_foo.py -xvs      # sole/inside wt
        ai-hats wt exec task/hats-1 -- ruff check src/        # pick a worktree
        ai-hats wt exec task/hats-1 -C packages/ai-hats-wt -- pytest  # a subproject
        ai-hats wt exec task/hats-1 python -c 'import ai_hats'
    """
    args = list(cmd_args)
    # HATS-1213: peel the selector BEFORE resolving — cwd (and the sole-active
    # convenience) used to silently beat an explicit `wt exec <branch>`. With no
    # selector this is the old no-arg call, ambiguity refusal included.
    mgr = _resolve_worktree(_peel_selector(args))

    if mgr is None:
        console.print("[yellow]No active worktree[/]")
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        console.print("[red]Active worktree has no path[/]")
        sys.exit(1)

    from ai_hats_wt import workspace_pythonpath

    # HATS-887: strip GIT_* plumbing so `ai-hats wt exec -- git …` resolves from
    # the worktree (cwd), not an ambient GIT_DIR a merge/hook context exports.
    env = os.environ.copy()
    for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(_var, None)
    run_dir = _effective_dir(wt_path, subdir)
    # HATS-913: src alone Franken-mixes — packages/*/src must come from the
    # worktree. HATS-1205: rooted at whichever project owns run_dir.
    env["PYTHONPATH"] = workspace_pythonpath(
        _owner_root(run_dir, wt_path), env.get("PYTHONPATH", "")
    )

    try:
        result = subprocess.run(args, cwd=str(run_dir), env=env)
    except FileNotFoundError as e:
        console.print(f"[red]Command not found:[/] {e.filename}")
        sys.exit(127)
    sys.exit(result.returncode)


@wt.command("env")
@click.argument("branch", required=False)
def wt_env(branch: str | None):
    """Print shell exports for a worktree (eval-friendly).

    Without BRANCH: inferred from CWD, else the sole active worktree.

    \b
        eval "$(ai-hats wt env)"
        eval "$(ai-hats wt env task/hats-1)"   # reach into another worktree
        # now $WT and $PYTHONPATH are set; cd to it manually if needed
    """
    mgr = _resolve_worktree(branch)
    if mgr is None:
        click.echo("# no active worktree", err=True)
        sys.exit(1)

    wt_path = mgr.worktree_path
    if wt_path is None:
        click.echo("# active worktree has no path", err=True)
        sys.exit(1)

    from ai_hats_wt import workspace_pythonpath

    click.echo(f'export WT="{wt_path}"')
    paths = workspace_pythonpath(wt_path)
    click.echo(f'export PYTHONPATH="{paths}${{PYTHONPATH:+:$PYTHONPATH}}"')
