"""`ai-hats task` — manage task cards and the brainstorm→done state machine."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ai_hats_core import atomic_write_text
from ..state import EmptyPlanError
from . import _seam


def _resolve_description(
    description: str | None, description_file: str | None, *, default: str | None
) -> str | None:
    """Resolve a task description from ``-d``/``--description`` or ``--description-file``.

    The two are mutually exclusive. ``--description-file`` reads the file
    verbatim via :meth:`Path.read_text`, sidestepping the shell-quoting /
    heredoc hazards that silently truncate ``-d "$(cat <<EOF …)"`` (HATS-492).
    Returns ``default`` when neither option is supplied.
    """
    if description is not None and description_file is not None:
        raise click.UsageError("--description and --description-file are mutually exclusive")
    if description_file is not None:
        try:
            return Path(description_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.UsageError(
                f"--description-file: cannot read {description_file!r}: {exc.strerror or exc}"
            )
    if description is not None:
        return description
    return default


@click.group()
def task():
    """Manage task cards and state machine."""
    pass


@task.command("create")
@click.argument("title")
@click.option("--id", "task_id", default=None, help="Task ID (auto-generated if omitted)")
@click.option("--description", "-d", default=None, help="Task description")
@click.option(
    "--description-file",
    "description_file",
    default=None,
    help="Read description from a file (verbatim, bypasses shell quoting). "
    "Mutually exclusive with -d/--description.",
)
@click.option("--priority", "-p", default="medium", help="Priority (low/medium/high)")
@click.option("--role", default="", help="Assigned role")
@click.option("--reviewer", default="user", help="Reviewer (user or agent)")
@click.option("--tag", multiple=True, help="Tags")
@click.option(
    "--parent-task",
    "parent_task",
    default="",
    help="Parent task ID (composition / epic→child relationship)",
)
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="Blocker task IDs (this task is blocked until each is done). Repeatable.",
)
def task_create(
    task_id: str | None,
    title: str,
    description: str | None,
    description_file: str | None,
    priority: str,
    role: str,
    reviewer: str,
    tag: tuple,
    parent_task: str,
    depends_on: tuple,
):
    """Create a new task card. ID is auto-generated if omitted."""

    description = _resolve_description(description, description_file, default="")
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    # task_id is None when the user gave no --id: let create_task allocate it
    # atomically under the alloc lock (HATS-936) rather than a racy pre-read.
    try:
        t, auto = mgr.create_task(
            task_id,
            title,
            description=description,
            priority=priority,
            role=role,
            reviewer=reviewer,
            parent_task=parent_task,
            depends_on=list(depends_on),
            tags=list(tag),
        )
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _warn_missing_refs(mgr, parent_task, list(depends_on))
    _seam._CONSOLE.print(f"[green]Created[/]: {t.id} — {t.title} [{t.state.value}] ({t.priority})")
    _print_auto_transitions(auto)


def _print_auto_transitions(transitions) -> None:
    """Surface harness-driven epic auto-transitions (HATS-690).

    The manager returns a list of :class:`TaskTransition` deltas alongside the
    primary card; print one user-facing notice per delta so an auto-advance /
    reopen of a parent epic is never silent.
    """
    for tr in transitions:
        _seam._CONSOLE.print(
            f"  [cyan]Epic auto-transition[/]: {tr.ticket.id} "
            f"{tr.from_state.value} → {tr.to_state.value} ({tr.reason})"
        )


def _warn_missing_refs(mgr, parent: str, depends: list[str]) -> None:
    """Print a yellow warning for refs that point at non-existent tasks.

    Non-fatal — typos and forward-references happen, the user can fix
    them later via `task update`. Surfacing here at the CLI edge keeps
    the manager API pure (no print side effects).
    """
    refs = ([parent] if parent else []) + depends
    missing = mgr.missing_refs(refs)
    if missing:
        _seam._CONSOLE.print(f"[yellow]Warning[/]: unknown ref(s): {', '.join(missing)}")


@task.command("transition")
@click.argument("task_id")
@click.argument("new_state")
@click.option(
    "--final-state", default=None, help="Final accomplished state (for review transition)"
)
@click.option(
    "--resolution",
    default=None,
    help="Resolution note (required for cancelled — why the task is being closed)",
)
@click.option(
    "--force",
    is_flag=True,
    help="Bypass FSM guard for corrective overrides (requires --reason)",
)
@click.option(
    "--reason",
    default=None,
    help="Reason for a --force transition (recorded in work_log)",
)
def task_transition(
    task_id: str,
    new_state: str,
    final_state: str | None,
    resolution: str | None,
    force: bool,
    reason: str | None,
):
    """Transition a task to a new state."""
    from ..models import TaskState

    try:
        from ai_hats_wt import (
            WorktreeBaseBranchError,
            WorktreeBaseBranchMismatchError,
            WorktreeCreateError,
            WorktreeDriftError,
            WorktreeMainRepoMidMergeError,
            WorktreeMergeConsentError,
            WorktreeStateIncompleteError,
            WorktreeStateLostError,
        )
    except ImportError:
        # wt-optional (HATS-934): without ai-hats-wt the wt-free TaskManager
        # raises none of these, so the except-ladder below just needs concrete
        # types to name. Distinct sentinels keep every `except` reachable.
        class WorktreeBaseBranchError(Exception): ...

        class WorktreeBaseBranchMismatchError(Exception): ...

        class WorktreeCreateError(Exception): ...

        class WorktreeDriftError(Exception): ...

        class WorktreeMainRepoMidMergeError(Exception): ...

        class WorktreeMergeConsentError(Exception): ...

        class WorktreeStateIncompleteError(Exception): ...

        class WorktreeStateLostError(Exception): ...

    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        state = TaskState(new_state)
    except ValueError:
        _seam._CONSOLE.print(f"[red]Invalid state[/]: {new_state}")
        _seam._CONSOLE.print(f"Valid states: {[s.value for s in TaskState]}")
        sys.exit(1)

    if state == TaskState.CANCELLED and not (resolution and resolution.strip()):
        _seam._CONSOLE.print(
            "[red]Error[/]: --resolution is required when transitioning to cancelled "
            "(record why: duplicate, won't-fix, obsolete, etc.)"
        )
        sys.exit(1)

    if force and not (reason and reason.strip()):
        _seam._CONSOLE.print(
            "[red]Error[/]: --force requires --reason (the override is "
            "recorded in work_log for audit)"
        )
        sys.exit(1)

    # HATS-723: `--final-state` is the accomplished-work summary recorded for
    # the review transition. Reject it loudly on any other target rather than
    # parsing and silently dropping it (option-parsed-then-ignored class).
    if final_state and state != TaskState.REVIEW:
        _seam._CONSOLE.print(
            "[red]Error[/]: --final-state is only valid with the review "
            f"transition (got target '{state.value}')"
        )
        sys.exit(1)

    # HATS-788: done/failed/cancelled tear down the task's worktree
    # (`_remove_worktree` via merge or discard). If the operator's shell is
    # inside that worktree, the teardown deletes its own cwd and every later
    # `ai-hats` mis-resolves the tracker. Refuse before the manager runs;
    # the guard prints the `cd <main-checkout>` recovery.
    if state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
        _seam._GUARD_LINKED_WT()

    try:
        # final_state rides the transition's lock window (HATS-723) so a failed
        # transition never leaves a half-applied summary on the card.
        # HATS-840: raw cwd for the execute-state worktree adopt (project_dir is
        # already main-hopped, so the library can't read cwd itself).
        caller_cwd = Path.cwd()
        t, auto = mgr.transition(
            task_id,
            state,
            resolution=resolution,
            final_state=final_state,
            force=force,
            reason=reason,
            caller_cwd=caller_cwd,
        )
        prefix = "[yellow]Forced[/]" if force else "[green]Transitioned[/]"
        _seam._CONSOLE.print(f"{prefix}: {t.id} → {t.state.value}")
        if force:
            _seam._CONSOLE.print(f"  Reason: {reason}")
        if state == TaskState.PLAN:
            plan_path = mgr.tasks_dir / task_id / "plan.md"
            if plan_path.exists():
                _seam._CONSOLE.print(f"  Plan scaffold: {plan_path}")
        elif state == TaskState.EXECUTE:
            try:
                from ai_hats_wt import WorktreeManager
            except ImportError:
                WorktreeManager = None  # wt-free: no worktree to display

            if WorktreeManager is not None and _seam._WORKTREES_DIR is not None:
                project_dir = _seam._PROJECT_DIR()
                active = WorktreeManager.load_for_task(
                    project_dir, task_id, state_dir=_seam._WORKTREES_DIR(project_dir)
                )
                if active and active.worktree_path:
                    _seam._CONSOLE.print(f"  Worktree: {active.worktree_path}")
                    _seam._CONSOLE.print(f"  Branch: {active.branch_name}")
                    _seam._CONSOLE.print(f"  [dim]cd {active.worktree_path}[/]")
                elif WorktreeManager.is_inside_linked_worktree(caller_cwd):
                    # HATS-060 / HATS-840: adopted the caller's worktree (detect via cwd).
                    adopted = WorktreeManager.worktree_toplevel(caller_cwd) or caller_cwd
                    _seam._CONSOLE.print(f"  Worktree: {adopted} [dim](adopted — already cwd)[/]")
                elif force:
                    # HATS-697: a forced execute is a manual state correction and
                    # deliberately creates no worktree.
                    _seam._CONSOLE.print(
                        "  [dim]No worktree created (forced) — "
                        "`ai-hats wt create` if you want isolation.[/]"
                    )
        elif state == TaskState.DONE:
            _seam._CONSOLE.print("  Worktree merged")
        elif state == TaskState.FAILED:
            _seam._CONSOLE.print("  Worktree discarded")
        elif state == TaskState.CANCELLED:
            _seam._CONSOLE.print(f"  Resolution: {t.resolution}")
            _seam._CONSOLE.print("  Worktree discarded")
        _print_auto_transitions(auto)
    except EmptyPlanError as e:
        _seam._CONSOLE.print(
            f"[red]Plan is incomplete[/] — cannot transition {e.task_id} to execute."
        )
        if e.empty_sections:
            _seam._CONSOLE.print(
                f"  Empty required section(s): [yellow]{', '.join(e.empty_sections)}[/]"
            )
        _seam._CONSOLE.print(f"  Plan path: {e.plan_path}")
        _seam._CONSOLE.print("  Fill the named section(s) in plan.md, then retry.")
        sys.exit(2)
    except WorktreeMergeConsentError as e:
        # HATS-1019: the deny doubles as the review-handoff directive.
        # Card stays in its prior state; after the supervisor merges, the
        # retried transition hits the already-merged short-circuit ack-free.
        from rich.markup import escape as _escape

        _seam._CONSOLE.print(
            f"[red]Refused (supervisor consent required)[/] — cannot merge for {task_id}."
        )
        _seam._CONSOLE.print(_escape(str(e)))
        _seam._CONSOLE.print(
            "The task is ready for review — STOP and hand it off to the "
            "supervisor. After reviewing, the supervisor merges; then retry:"
        )
        _seam._CONSOLE.print(
            # e.branch_name, not task_id: `wt merge` resolves branches (PROP-042)
            f"  [cyan]AI_HATS_MERGE_ACK=1 ai-hats wt merge {e.branch_name}[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        sys.exit(1)
    except WorktreeBaseBranchError as e:
        # HATS-518: main-repo HEAD wasn't on a canonical base when we tried
        # to create the execute worktree. Card stays in its prior state —
        # the transition's _save_task was never reached.
        _seam._CONSOLE.print(f"[red]{e}[/]")
        sys.exit(1)
    except WorktreeBaseBranchMismatchError as e:
        # HATS-533: main-repo HEAD wandered off the merge target captured
        # at create time. The internal `wt merge` would otherwise land on
        # the current branch — same wrong-branch-merge class as HATS-486.
        # Card stays in `review` (HATS-481 fail-loud).
        from rich.markup import escape as _escape

        project_dir = _seam._PROJECT_DIR()
        _seam._CONSOLE.print(
            f"[red]Refused (base branch mismatch)[/] — cannot merge for {task_id}."
        )
        _seam._CONSOLE.print(_escape(str(e)))
        _seam._CONSOLE.print("")
        _seam._CONSOLE.print("Switch the main repo to the merge target, then retry:")
        # soft_wrap=True so long main-repo paths stay on one line for
        # copy-paste (HATS-509 precedent).
        _seam._CONSOLE.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        _seam._CONSOLE.print(
            f"  [cyan]git checkout {_escape(e.expected)}[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        sys.exit(1)
    except WorktreeMainRepoMidMergeError as e:
        # HATS-587 / F4: the main repo already has an unfinished merge in
        # progress (foreign MERGE_HEAD). The internal `wt merge` refuses
        # BEFORE any mutation, so the worktree and branch are untouched and
        # the card stays in `review` (HATS-481 fail-loud). Surface a clean
        # resolve recipe instead of a raw exit-128 traceback.
        from rich.markup import escape as _escape

        project_dir = _seam._PROJECT_DIR()
        _seam._CONSOLE.print(f"[red]Refused (main repo mid-merge)[/] — cannot merge for {task_id}.")
        _seam._CONSOLE.print(_escape(str(e)))
        _seam._CONSOLE.print("")
        _seam._CONSOLE.print("Resolve the in-progress merge first, then retry:")
        _seam._CONSOLE.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        _seam._CONSOLE.print(
            "  [cyan]git merge --abort[/]  [dim]# or resolve conflicts + git commit[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        sys.exit(1)
    except WorktreeStateIncompleteError as e:
        # HATS-714: `task transition done` shares `WorktreeManager.merge`, so a
        # state file lacking `original_branch` would re-traceback here just as
        # it did on `wt merge`. Surface the same typed refusal. The card stays
        # in `review` (HATS-481 fail-loud: the raise precedes `_save_task`).
        from rich.markup import escape as _escape

        _seam._CONSOLE.print(f"[red]Refused (incomplete worktree state)[/]: {_escape(str(e))}")
        sys.exit(1)
    except WorktreeStateLostError as e:
        # State JSON gone, branch preserved → card stays in `review`. Post
        # HATS-697 an already-merged branch auto-finalizes, so reaching here
        # means the branch genuinely diverges (un-merged wording is accurate).
        from rich.markup import escape as _escape

        project_dir = _seam._PROJECT_DIR()
        _seam._CONSOLE.print(
            f"[red]Refused (worktree state lost)[/] — task {task_id} "
            f"cannot be silently marked DONE."
        )
        _seam._CONSOLE.print(
            f"Branch '{_escape(e.branch_name)}' has commits that are NOT in "
            f"the base branch (an already-merged branch would finalize on its "
            f"own — HATS-697)."
        )
        _seam._CONSOLE.print(
            "Likely cause: the auto-worktree was removed by hand, or an "
            "earlier `task transition <id> done` attempt's merge failed "
            "(conflict, lock contention, untracked file collision)."
        )
        _seam._CONSOLE.print("")
        _seam._CONSOLE.print("Apply the un-merged work, then finalize:")
        # soft_wrap=True keeps each recipe line intact for copy-paste.
        _seam._CONSOLE.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        _seam._CONSOLE.print(
            "  [cyan]git merge --abort[/]  [dim]# if main repo is mid-merge[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            f"  [cyan]git merge --no-ff {_escape(e.branch_name)}[/]  "
            "[dim]# apply the un-merged work[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]  [dim]# update tracker[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print("")
        _seam._CONSOLE.print(
            f"[dim]Abandoning the work instead? "
            f"`git branch -D {_escape(e.branch_name)}` then re-run done, or "
            f"`task transition {task_id} cancelled`.[/]",
            soft_wrap=True,
        )
        sys.exit(1)
    except WorktreeDriftError as e:
        # HATS-509: translate the inner `wt merge` drift error for
        # `task transition <ID> done` callers. After HATS-509 Step 2 the
        # exception body carries facts only (drift summary, commits,
        # paths); the user-facing recipe is owned by CLI handlers. We
        # re-emit with an explicit two-step recipe pointing at the
        # main-repo path and clarifying the flag belongs to `wt merge`,
        # not the current command — the original "re-run with
        # --accept-drift" wording would have been copy-pasted as a
        # `task transition` flag, which does NOT exist.
        #
        # Card stays in `review`: HATS-481 fail-loud guarantees the
        # worktree-effect teardown raise propagates before `_save_task`.
        from rich.markup import escape as _escape

        project_dir = _seam._PROJECT_DIR()
        _seam._CONSOLE.print(
            f"[red]Worktree drifted vs original branch[/] — cannot merge for {task_id}."
        )
        # Preserve the drift summary verbatim (commits + affected paths
        # from WorktreeManager._drift_summary) — it's informational.
        # Escape: drift summary embeds filenames, and a hostile filename
        # like `[red]boom[/]` would inject Rich markup into the
        # operator's terminal. Mirrors cli/worktree.py wt_merge handler.
        _seam._CONSOLE.print(_escape(str(e)))
        _seam._CONSOLE.print("")
        _seam._CONSOLE.print("Re-verify your changes against the new base, then run:")
        # soft_wrap=True keeps each recipe line intact for copy-paste
        # even on narrow terminals (default Rich console width when
        # stdout is a pipe is 80 cols, which truncates long project paths).
        _seam._CONSOLE.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        _seam._CONSOLE.print("  [cyan]ai-hats wt merge --accept-drift[/]", soft_wrap=True)
        _seam._CONSOLE.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        _seam._CONSOLE.print(
            "[dim]Note: --accept-drift belongs to `wt merge`, not `task transition`.[/]"
        )
        sys.exit(1)
    except WorktreeCreateError as e:
        # HATS-517: defense-in-depth handler for the branch-exists
        # classifier inside WorktreeManager.create() — Case B (branch
        # checked out in MAIN worktree). In practice HATS-518's
        # canonical-base guard fires earlier when the operator is on
        # `task/<id>`; this handler catches Case B residuals (non-base
        # branches not covered by HATS-518) and any other create-time
        # refusal the classifier might raise.
        #
        # Distinct exit code 2 separates "worktree setup refused with
        # actionable hint" from FSM / validation exit 1. The exception
        # message already carries multi-line guidance — print verbatim.
        _seam._CONSOLE.print(f"[red]Cannot create worktree[/]: {e}")
        sys.exit(2)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("close")
@click.argument("task_id")
@click.option(
    "--resolution",
    required=True,
    help="Why the task is being fast-closed (shipped on master, subsumed, etc.)",
)
def task_close(task_id: str, resolution: str):
    """Fast-close a task from brainstorm/plan straight to done.

    Use when the work has already shipped (e.g. direct commit on master)
    and the full execute/document/review walk would just be bookkeeping.
    """
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        t, auto = mgr.close_task(task_id, resolution)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _seam._CONSOLE.print(f"[green]Closed[/]: {t.id} → {t.state.value}")
    _seam._CONSOLE.print(f"  Resolution: {t.resolution}")
    _print_auto_transitions(auto)


@task.command("link")
@click.argument("from_id")
@click.argument("to_id")
@click.option(
    "--type",
    "link_type",
    type=click.Choice(["related", "see-also", "fold"]),
    default="related",
    help="Link kind (default: related)",
)
def task_link(from_id: str, to_id: str, link_type: str):
    """Cross-reference two task cards.

    \b
    related   — symmetric "see also without blocking" (default)
    see-also  — symmetric, lighter cross-reference
    fold      — directional: FROM is folded into TO (FROM.folded_into = TO)
    """
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        a, _ = mgr.add_link(from_id, to_id, link_type=link_type)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    if link_type == "fold":
        _seam._CONSOLE.print(f"[green]Folded[/]: {a.id} → {to_id}")
    else:
        # Parens around link_type — rich would strip `[related]` as malformed markup.
        _seam._CONSOLE.print(f"[green]Linked[/] ({link_type}): {from_id} ↔ {to_id}")


@task.command("unlink")
@click.argument("from_id")
@click.argument("to_id")
@click.option(
    "--type",
    "link_type",
    type=click.Choice(["related", "see-also", "fold"]),
    default="related",
    help="Link kind to remove (default: related)",
)
def task_unlink(from_id: str, to_id: str, link_type: str):
    """Remove a cross-reference between two task cards. No-op if absent."""
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        mgr.remove_link(from_id, to_id, link_type=link_type)
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _seam._CONSOLE.print(f"[green]Unlinked[/] ({link_type}): {from_id} ⇎ {to_id}")


@task.command("plan-extract")
@click.argument("task_id")
@click.option("--auto", is_flag=True, help="Skip prompts; create all candidates.")
@click.option("--dry-run", is_flag=True, help="Print candidates without mutating.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON; implies --dry-run.")
def task_plan_extract(task_id: str, auto: bool, dry_run: bool, as_json: bool):
    """Parse plan.md and create child task cards from its structured sections.

    Looks for `## Subtasks` bullets, `## Steps` checklist items, or numbered
    level-3 headings (in that priority order). Marks each processed line in
    plan.md with `<!-- HATS-NNN -->` so re-runs are idempotent.
    """
    import json

    from ..plan_extract import Candidate, extract_candidates, mark_extracted

    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    task = mgr.get_task(task_id)
    if task is None:
        _seam._CONSOLE.print(f"[red]Error[/]: task {task_id} not found")
        sys.exit(1)

    plan_path = mgr.tasks_dir / task_id / "plan.md"
    if not plan_path.exists():
        _seam._CONSOLE.print(f"[red]Error[/]: plan.md not found at {plan_path}")
        sys.exit(1)
    if mgr._is_empty_scaffold(task):
        _seam._CONSOLE.print(
            f"[red]Plan is empty scaffold[/] — nothing to extract from {plan_path}"
        )
        sys.exit(2)

    plan_text = plan_path.read_text()
    candidates = extract_candidates(plan_text)
    if not candidates:
        _seam._CONSOLE.print(
            "No candidates found (looked for `## Subtasks`, `## Steps`, "
            "numbered `### N. …` / `### Phase N: …` headings)."
        )
        sys.exit(0)

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "line_no": c.line_no,
                        "title": c.title,
                        "kind": c.kind,
                        "raw_line": c.raw_line,
                    }
                    for c in candidates
                ]
            )
        )
        sys.exit(0)

    if dry_run:
        _seam._CONSOLE.print(f"[dim]Found {len(candidates)} candidate(s):[/]")
        for c in candidates:
            _seam._CONSOLE.print(f"  [{c.kind}] line {c.line_no}: {c.title}")
        sys.exit(0)

    selected: list[tuple[Candidate, str]] = []  # (candidate, final_title)
    for c in candidates:
        if auto:
            selected.append((c, c.title))
            continue
        _seam._CONSOLE.print(f"\n[{c.kind}] line {c.line_no}: [bold]{c.title}[/]")
        choice = (
            click.prompt(
                "  [y]es / [n]o / [e]dit / [q]uit",
                default="n",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if choice in ("q", "quit"):
            break
        if choice in ("n", "no", ""):
            continue
        if choice in ("e", "edit"):
            new_title = click.prompt("  Title", default=c.title).strip()
            if not new_title:
                continue
            selected.append((c, new_title))
        else:  # any "y"/"yes" path
            selected.append((c, c.title))

    if not selected:
        _seam._CONSOLE.print("Nothing extracted.")
        sys.exit(0)

    text = plan_text
    created: list[str] = []
    for cand, title in selected:
        try:
            # Atomic allocate+reserve (HATS-936); read the id back off the card.
            child, _ = mgr.create_task(
                task_id=None,
                title=title,
                priority="medium",
                tags=["extracted-from-plan"],
                parent_task=task_id,
            )
            child_id = child.id
        except Exception as exc:  # pragma: no cover — defensive only
            _seam._CONSOLE.print(f"[red]Failed to create task for[/] {title!r}: {exc}")
            continue
        text = mark_extracted(text, cand.line_no, child_id)
        created.append(f"{child_id}: {title}")

    if created:
        atomic_write_text(plan_path, text)
        _seam._CONSOLE.print(f"\n[green]Created {len(created)} subtask(s):[/]")
        for line in created:
            _seam._CONSOLE.print(f"  {line}")


@task.command("update")
@click.argument("task_id")
@click.option("--title", default=None, help="New title")
@click.option("--description", "-d", default=None, help="New description")
@click.option(
    "--description-file",
    "description_file",
    default=None,
    help="Read description from a file (verbatim, bypasses shell quoting). "
    "Mutually exclusive with -d/--description.",
)
@click.option(
    "--priority", "-p", default=None, type=click.Choice(["low", "medium", "high"]), help="Priority"
)
@click.option("--resolution", default=None, help="Resolution note (why closed)")
@click.option("--role", default=None, help="Assigned role")
@click.option("--reviewer", default=None, help="Reviewer")
@click.option("--add-tag", multiple=True, help="Add tag")
@click.option("--remove-tag", multiple=True, help="Remove tag")
@click.option(
    "--parent-task",
    "parent_task",
    default=None,
    help="Set parent task ID (composition / epic→child relationship)",
)
@click.option(
    "--clear-parent",
    is_flag=True,
    help="Clear the parent task reference",
)
@click.option(
    "--add-depends",
    multiple=True,
    help="Add a blocker task ID (this task is blocked until that one is done)",
)
@click.option(
    "--remove-depends",
    multiple=True,
    help="Remove a blocker task ID",
)
def task_update(
    task_id: str,
    title: str | None,
    description: str | None,
    description_file: str | None,
    priority: str | None,
    resolution: str | None,
    role: str | None,
    reviewer: str | None,
    add_tag: tuple,
    remove_tag: tuple,
    parent_task: str | None,
    clear_parent: bool,
    add_depends: tuple,
    remove_depends: tuple,
):
    """Update task card fields."""

    description = _resolve_description(description, description_file, default=None)
    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())

    if clear_parent and parent_task is not None:
        _seam._CONSOLE.print(
            "[red]Error[/]: --clear-parent and --parent-task are mutually exclusive"
        )
        sys.exit(1)
    parent_arg = "" if clear_parent else parent_task

    has_changes = any(
        [
            title,
            description,
            priority,
            resolution,
            role,
            reviewer,
            add_tag,
            remove_tag,
            parent_arg is not None,
            add_depends,
            remove_depends,
        ]
    )
    if not has_changes:
        _seam._CONSOLE.print(
            "[yellow]No changes specified[/]. Use --title, --priority, --description, etc."
        )
        return

    try:
        t, auto = mgr.update_task(
            task_id,
            title=title,
            description=description,
            priority=priority,
            resolution=resolution,
            role=role,
            reviewer=reviewer,
            add_tags=list(add_tag) if add_tag else None,
            remove_tags=list(remove_tag) if remove_tag else None,
            parent_task=parent_arg,
            add_depends=list(add_depends) if add_depends else None,
            remove_depends=list(remove_depends) if remove_depends else None,
        )
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _warn_missing_refs(mgr, parent_arg or "", list(add_depends))
    _seam._CONSOLE.print(f"[green]Updated[/]: {t.id} — {t.title} [{t.priority}]")
    _print_auto_transitions(auto)


@task.command("log")
@click.argument("task_id")
@click.argument("message")
@click.option("--session", default=None, help="Session ID (defaults to AI_HATS_SESSION_ID)")
def task_log(task_id: str, message: str, session: str | None):
    """Log work progress on a task."""

    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    try:
        t = mgr.log_work(task_id, message, session_id=session or "")
        _seam._CONSOLE.print(f"[green]Logged[/]: {t.id} — {message}")
    except ValueError as e:
        _seam._CONSOLE.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("list")
@click.option("--state", default=None, help="Filter by state")
@click.option("--priority", default=None, help="Filter by priority (low/medium/high)")
@click.option("--all", "-a", "show_all", is_flag=True, help="Include done/failed tasks")
@click.option(
    "--reclaimable",
    is_flag=True,
    default=False,
    help="Only execute-state tasks whose owner is dead/absent (safe to pick up)",
)
@click.option(
    "--search",
    "-s",
    default=None,
    help="Regex search across id, title, description, tags, parent_task, depends_on",
)
def task_list(
    state: str | None,
    priority: str | None,
    show_all: bool,
    reclaimable: bool,
    search: str | None,
):
    """List all task cards."""
    import re as _re

    from rich.table import Table

    from ..models import TaskState

    STATE_ORDER = {
        TaskState.EXECUTE: 0,
        TaskState.DOCUMENT: 1,
        TaskState.REVIEW: 2,
        TaskState.PLAN: 3,
        TaskState.BRAINSTORM: 4,
        TaskState.BLOCKED: 5,
        TaskState.DONE: 6,
        TaskState.FAILED: 7,
        TaskState.CANCELLED: 8,
    }
    PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    filter_state = TaskState(state) if state else None
    tasks = mgr.list_tasks(state=filter_state, priority=priority)

    if not show_all and filter_state is None:
        tasks = [
            t
            for t in tasks
            if t.state not in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
        ]

    if reclaimable:
        tasks = [
            t
            for t in tasks
            if t.state == TaskState.EXECUTE
            and ((o := mgr.ownership_of(t.id)) is None or not o.get("is_live"))
        ]

    if search:
        try:
            pattern = _re.compile(search, _re.IGNORECASE)
        except _re.error as e:
            _seam._CONSOLE.print(f"[red]Bad regex[/]: {e}")
            sys.exit(1)
        tasks = [
            t
            for t in tasks
            if pattern.search(
                "\n".join(
                    [
                        t.id,
                        t.title,
                        t.description,
                        t.parent_task,
                        *t.tags,
                        *t.depends_on,
                        *t.related,
                        *t.see_also,
                        t.folded_into,
                    ]
                )
            )
        ]

    if not tasks:
        _seam._CONSOLE.print("[dim]No tasks[/]")
        return

    tasks.sort(key=lambda t: (STATE_ORDER.get(t.state, 99), PRIORITY_ORDER.get(t.priority, 99)))

    table = Table(show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Pri", no_wrap=True)
    table.add_column("Title")
    table.add_column("Parent", style="dim")

    state_styles = {
        TaskState.EXECUTE: "bold green",
        TaskState.PLAN: "yellow",
        TaskState.BRAINSTORM: "dim",
        TaskState.BLOCKED: "bold red",
        TaskState.DONE: "dim green",
        TaskState.FAILED: "dim red",
        TaskState.CANCELLED: "dim yellow",
    }

    for t in tasks:
        style = state_styles.get(t.state, "")
        table.add_row(
            t.id,
            f"[{style}]{t.state.value}[/{style}]" if style else t.state.value,
            t.priority,
            t.title,
            t.parent_task or "",
        )

    _seam._CONSOLE.print(table)


@task.command("show")
@click.argument("task_id")
@click.option(
    "--short",
    is_flag=True,
    default=False,
    help="Compact view: card + link index (id/state/title) only, without the "
    "full linked-task bodies. Default shows the linked context a sub-agent gets.",
)
def task_show(task_id: str, short: bool):
    """Show task card details.

    By default (HATS-691) the card is followed by a ``Linked context`` block —
    the trimmed cards of every direct link (parent epic + its ``plan.md``,
    plus ``depends_on`` / ``related`` / ``see_also``), the same content a
    sub-agent receives via the runtime ``LINKED_CONTEXT`` injection. Pass
    ``--short`` for the compact id/state/title index only.
    """

    from rich.markup import escape as _escape

    project_dir = _seam._PROJECT_DIR()
    mgr = _seam._MANAGER_FACTORY(project_dir)
    t = mgr.get_task(task_id)
    if t is None:
        _seam._CONSOLE.print(f"[red]Task not found[/]: {task_id}")
        sys.exit(1)
    # markup=False: card field values (title / description / work_log …) are
    # user-controlled; without it a card titled `[red]X[/]` would be eaten /
    # recolored as Rich markup. The dump loop carries no intentional markup.
    for k, v in t.to_dict().items():
        if v:
            _seam._CONSOLE.print(f"  {k}: {v}", markup=False, highlight=False)
    # Resolve depends_on into "Blocked by:" with each blocker's current state.
    # The raw `depends_on: [PROJ-X, PROJ-Y]` line above is opaque on its own —
    # this section answers "is this task actually unblocked yet?".
    if t.depends_on:
        _seam._CONSOLE.print("\n  [bold]Blocked by:[/]")
        for dep_id in t.depends_on:
            dep = mgr.get_task(dep_id)
            if dep is None:
                _seam._CONSOLE.print(f"    {dep_id} [red](missing)[/]")
            else:
                # Use parens around state — rich interprets `[brainstorm]` as a
                # malformed markup tag and silently drops it. Escape the title:
                # it is user-controlled and sits on an intentional-markup line.
                _seam._CONSOLE.print(f"    {dep_id} ({dep.state.value}) — {_escape(dep.title)}")

    # Link sections — render each outbound relation with the target's state
    # so the cross-reference answers "is this still relevant?" at a glance.
    def _render_links(label: str, ids: list[str]) -> None:
        _seam._CONSOLE.print(f"\n  [bold]{label}:[/]")
        for ref_id in ids:
            other = mgr.get_task(ref_id)
            if other is None:
                _seam._CONSOLE.print(f"    {ref_id} [red](missing)[/]")
            else:
                _seam._CONSOLE.print(f"    {ref_id} ({other.state.value}) — {_escape(other.title)}")

    if t.related:
        _render_links("Related", t.related)
    if t.see_also:
        _render_links("See also", t.see_also)
    if t.folded_into:
        target = mgr.get_task(t.folded_into)
        _seam._CONSOLE.print("\n  [bold]Folded into:[/]")
        if target is None:
            _seam._CONSOLE.print(f"    {t.folded_into} [red](missing)[/]")
        else:
            _seam._CONSOLE.print(
                f"    {t.folded_into} ({target.state.value}) — {_escape(target.title)}"
            )
    # Inbound "Subsumed:" — scan all cards for folded_into pointing here.
    subsumed = mgr.find_subsumed_by(task_id)
    if subsumed:
        _render_links("Subsumed", subsumed)

    # Show work log nicely
    if t.work_log:
        _seam._CONSOLE.print("\n  [bold]Work Log:[/]")
        for entry in t.work_log:
            _seam._CONSOLE.print(f"    {entry.timestamp} — {entry.message}")

    # Linked context (HATS-691): by default, append the full linked-task bodies
    # — parity with the sub-agent's LINKED_CONTEXT (HATS-689). The link index
    # above only names the cross-references; this block carries their content so
    # the reader (human or interactive agent via `task show`) sees the same thing
    # a spawned sub-agent gets. `--short` skips it.
    if not short:
        from ..linked_context import load_linked_context

        linked = load_linked_context(tasks_root=mgr.tasks_dir, ticket_id=task_id)
        if linked:
            _seam._CONSOLE.print("\n  [bold]Linked context:[/]")
            # markup=False: the body carries literal `[parent_task]` / `[related]`
            # tags and arbitrary card/plan text that rich would mis-parse as
            # markup (same hazard the link index avoids with parens, above).
            _seam._CONSOLE.print(linked, markup=False, highlight=False)
            # A parent epic's plan.md can run to tens of KB; hint at the compact
            # view when the block is long, without overriding the full-by-default
            # contract (HATS-691 Q1).
            if linked.count("\n") >= 30:
                _seam._CONSOLE.print("\n  [dim]tip: pass --short for the compact index only[/]")


@task.command("sync")
def task_sync():
    """Synchronize STATE.md with task cards."""

    mgr = _seam._MANAGER_FACTORY(_seam._PROJECT_DIR())
    count = mgr.sync()
    _seam._CONSOLE.print(f"[green]Synced[/]: {count} tasks")
