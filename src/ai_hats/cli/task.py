"""`ai-hats task` — manage task cards and the brainstorm→done state machine."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..state import EmptyPlanError, PlanSyncAmbiguousError
from ._helpers import _project_dir, _task_manager, console


@click.group()
def task():
    """Manage task cards and state machine."""
    pass


@task.command("create")
@click.argument("title")
@click.option("--id", "task_id", default=None, help="Task ID (auto-generated if omitted)")
@click.option("--description", "-d", default="", help="Task description")
@click.option("--priority", "-p", default="medium", help="Priority (low/medium/high)")
@click.option("--role", default="", help="Assigned role")
@click.option("--reviewer", default="user", help="Reviewer (user or agent)")
@click.option("--tag", multiple=True, help="Tags")
@click.option(
    "--parent-task", "parent_task", default="",
    help="Parent task ID (composition / epic→child relationship)",
)
@click.option(
    "--depends-on", "depends_on", multiple=True,
    help="Blocker task IDs (this task is blocked until each is done). Repeatable.",
)
def task_create(
    task_id: str | None,
    title: str,
    description: str,
    priority: str,
    role: str,
    reviewer: str,
    tag: tuple,
    parent_task: str,
    depends_on: tuple,
):
    """Create a new task card. ID is auto-generated if omitted."""

    mgr = _task_manager(_project_dir())
    if task_id is None:
        task_id = mgr.next_id()
    try:
        t = mgr.create_task(
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
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _warn_missing_refs(mgr, parent_task, list(depends_on))
    console.print(f"[green]Created[/]: {t.id} — {t.title} [{t.state.value}] ({t.priority})")


def _warn_missing_refs(mgr, parent: str, depends: list[str]) -> None:
    """Print a yellow warning for refs that point at non-existent tasks.

    Non-fatal — typos and forward-references happen, the user can fix
    them later via `task update`. Surfacing here at the CLI edge keeps
    the manager API pure (no print side effects).
    """
    refs = ([parent] if parent else []) + depends
    missing = mgr.missing_refs(refs)
    if missing:
        console.print(f"[yellow]Warning[/]: unknown ref(s): {', '.join(missing)}")


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
    from ..worktree import WorktreeBaseBranchError  # HATS-518
    from ..worktree import WorktreeBaseBranchMismatchError  # HATS-533
    from ..worktree import WorktreeCreateError  # HATS-517
    from ..worktree import WorktreeDriftError  # HATS-509
    from ..worktree import WorktreeStateLostError  # HATS-541

    mgr = _task_manager(_project_dir())
    try:
        state = TaskState(new_state)
    except ValueError:
        console.print(f"[red]Invalid state[/]: {new_state}")
        console.print(f"Valid states: {[s.value for s in TaskState]}")
        sys.exit(1)

    if state == TaskState.CANCELLED and not (resolution and resolution.strip()):
        console.print(
            "[red]Error[/]: --resolution is required when transitioning to cancelled "
            "(record why: duplicate, won't-fix, obsolete, etc.)"
        )
        sys.exit(1)

    if force and not (reason and reason.strip()):
        console.print(
            "[red]Error[/]: --force requires --reason (the override is "
            "recorded in work_log for audit)"
        )
        sys.exit(1)

    try:
        if final_state and state == TaskState.REVIEW:
            mgr.set_final_state(task_id, final_state)
        t = mgr.transition(
            task_id, state, resolution=resolution, force=force, reason=reason
        )
        prefix = "[yellow]Forced[/]" if force else "[green]Transitioned[/]"
        console.print(f"{prefix}: {t.id} → {t.state.value}")
        if force:
            console.print(f"  Reason: {reason}")
        if state == TaskState.PLAN:
            plan_path = mgr.tasks_dir / task_id / "plan.md"
            if plan_path.exists():
                console.print(f"  Plan scaffold: {plan_path}")
                if not mgr._is_empty_scaffold(t):
                    console.print(
                        f"  [green]Plan synced[/] from .claude/plans/ → {plan_path}"
                    )
        elif state == TaskState.EXECUTE:
            from ..worktree import WorktreeManager

            project_dir = _project_dir()
            active = WorktreeManager.load_for_task(project_dir, task_id)
            if active and active.worktree_path:
                console.print(f"  Worktree: {active.worktree_path}")
                console.print(f"  Branch: {active.branch_name}")
                console.print(f"  [dim]cd {active.worktree_path}[/]")
            elif WorktreeManager.is_inside_linked_worktree(project_dir):
                # HATS-060: adopted the caller's linked worktree.
                console.print(f"  Worktree: {project_dir} [dim](adopted — already cwd)[/]")
        elif state == TaskState.DONE:
            console.print("  Worktree merged")
        elif state == TaskState.FAILED:
            console.print("  Worktree discarded")
        elif state == TaskState.CANCELLED:
            console.print(f"  Resolution: {t.resolution}")
            console.print("  Worktree discarded")
    except PlanSyncAmbiguousError as e:
        console.print(
            f"[red]Plan sync ambiguous[/] for {e.task_id}: multiple candidates "
            f"in .claude/plans/"
        )
        for p in e.matches:
            console.print(f"  {p}")
        console.print(
            "Re-run with: [cyan]ai-hats task plan-sync "
            f"{e.task_id} --from-file <path>[/]"
        )
        sys.exit(2)
    except EmptyPlanError as e:
        console.print(
            f"[red]Plan is empty[/] — cannot transition {e.task_id} to execute."
        )
        console.print(f"  Plan path: {e.plan_path}")
        console.print(
            "  Either fill in the plan, or run "
            f"[cyan]ai-hats task plan-sync {e.task_id}[/] "
            "to import from .claude/plans/."
        )
        sys.exit(2)
    except WorktreeBaseBranchError as e:
        # HATS-518: main-repo HEAD wasn't on a canonical base when we tried
        # to create the execute worktree. Card stays in its prior state —
        # the transition's _save_task was never reached.
        console.print(f"[red]{e}[/]")
        sys.exit(1)
    except WorktreeBaseBranchMismatchError as e:
        # HATS-533: main-repo HEAD wandered off the merge target captured
        # at create time. The internal `wt merge` would otherwise land on
        # the current branch — same wrong-branch-merge class as HATS-486.
        # Card stays in `review` (HATS-481 fail-loud).
        from rich.markup import escape as _escape

        project_dir = _project_dir()
        console.print(
            f"[red]Refused (base branch mismatch)[/] — "
            f"cannot merge for {task_id}."
        )
        console.print(_escape(str(e)))
        console.print("")
        console.print("Switch the main repo to the merge target, then retry:")
        # soft_wrap=True so long main-repo paths stay on one line for
        # copy-paste (HATS-509 precedent).
        console.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        console.print(
            f"  [cyan]git checkout {_escape(e.expected)}[/]",
            soft_wrap=True,
        )
        console.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        sys.exit(1)
    except WorktreeStateLostError as e:
        # HATS-541: a prior failed merge orphaned the worktree state
        # (state.json + dir cleared by Worktree.merge() on failure),
        # but the branch is still preserved. _teardown_worktree refused
        # to silently mark the task DONE without merging. Card stays in
        # `review` (HATS-481 fail-loud propagation).
        from rich.markup import escape as _escape

        project_dir = _project_dir()
        console.print(
            f"[red]Refused (worktree state lost)[/] — task {task_id} "
            f"cannot be silently marked DONE."
        )
        console.print(
            f"Branch '{_escape(e.branch_name)}' is preserved with "
            f"un-merged commits."
        )
        console.print(
            "Likely cause: an earlier `task transition <id> done` "
            "attempt's merge failed (conflict, lock contention, "
            "untracked file collision)."
        )
        console.print("")
        console.print("Recover manually:")
        # soft_wrap=True keeps each recipe line intact for copy-paste.
        console.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        console.print(
            "  [cyan]git merge --abort[/]  "
            "[dim]# if main repo is mid-merge[/]",
            soft_wrap=True,
        )
        console.print(
            f"  [cyan]git merge --no-ff {_escape(e.branch_name)}[/]  "
            "[dim]# apply the un-merged work[/]",
            soft_wrap=True,
        )
        console.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]  "
            "[dim]# update tracker[/]",
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
        # `_teardown_worktree` raise propagates before `_save_task`.
        from rich.markup import escape as _escape

        project_dir = _project_dir()
        console.print(
            f"[red]Worktree drifted vs original branch[/] — "
            f"cannot merge for {task_id}."
        )
        # Preserve the drift summary verbatim (commits + affected paths
        # from WorktreeManager._drift_summary) — it's informational.
        # Escape: drift summary embeds filenames, and a hostile filename
        # like `[red]boom[/]` would inject Rich markup into the
        # operator's terminal. Mirrors cli/worktree.py wt_merge handler.
        console.print(_escape(str(e)))
        console.print("")
        console.print(
            "Re-verify your changes against the new base, then run:"
        )
        # soft_wrap=True keeps each recipe line intact for copy-paste
        # even on narrow terminals (default Rich console width when
        # stdout is a pipe is 80 cols, which truncates long project paths).
        console.print(f"  [cyan]cd {project_dir}[/]", soft_wrap=True)
        console.print(
            "  [cyan]ai-hats wt merge --accept-drift[/]", soft_wrap=True
        )
        console.print(
            f"  [cyan]ai-hats task transition {task_id} done[/]",
            soft_wrap=True,
        )
        console.print(
            "[dim]Note: --accept-drift belongs to `wt merge`, "
            "not `task transition`.[/]"
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
        console.print(f"[red]Cannot create worktree[/]: {e}")
        sys.exit(2)
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
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
    mgr = _task_manager(_project_dir())
    try:
        t = mgr.close_task(task_id, resolution)
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    console.print(f"[green]Closed[/]: {t.id} → {t.state.value}")
    console.print(f"  Resolution: {t.resolution}")


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
    mgr = _task_manager(_project_dir())
    try:
        a, _ = mgr.add_link(from_id, to_id, link_type=link_type)
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    if link_type == "fold":
        console.print(f"[green]Folded[/]: {a.id} → {to_id}")
    else:
        # Parens around link_type — rich would strip `[related]` as malformed markup.
        console.print(f"[green]Linked[/] ({link_type}): {from_id} ↔ {to_id}")


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
    mgr = _task_manager(_project_dir())
    try:
        mgr.remove_link(from_id, to_id, link_type=link_type)
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    console.print(f"[green]Unlinked[/] ({link_type}): {from_id} ⇎ {to_id}")


@task.command("plan-sync")
@click.argument("task_id")
@click.option(
    "--from-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit plan source (overrides .claude/plans/ auto-detection)",
)
def task_plan_sync(task_id: str, from_file: Path | None):
    """Move .claude/plans/<NN>-*.md into .agent/backlog/tasks/<id>/plan.md.

    Imports a Plan-mode artifact into the canonical task tree location.
    Refuses to overwrite a plan.md that already contains non-scaffold content.
    """
    import shutil

    mgr = _task_manager(_project_dir())
    task = mgr.get_task(task_id)
    if task is None:
        console.print(f"[red]Error[/]: task {task_id} not found")
        sys.exit(1)

    if from_file is not None:
        src = from_file
    else:
        matches = mgr.find_claude_plan_for_task(task_id)
        if len(matches) == 0:
            console.print(
                f"No plan candidate in .claude/plans/ for {task_id} "
                "(searched <NN>-*.md and <prefix>-<NN>-*.md)."
            )
            sys.exit(0)
        if len(matches) > 1:
            console.print(
                f"[red]Multiple matches[/] for {task_id} in .claude/plans/:"
            )
            for p in matches:
                console.print(f"  {p}")
            console.print("Re-run with --from-file <path> to disambiguate.")
            sys.exit(2)
        src = matches[0]

    dst = mgr.tasks_dir / task_id / "plan.md"
    if dst.exists() and not mgr._is_empty_scaffold(task):
        console.print(
            f"[red]Refusing to overwrite[/] non-scaffold plan: {dst}"
        )
        console.print("  Move or remove the existing file manually first.")
        sys.exit(2)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    console.print(f"[green]Synced[/]: {src} → {dst}")


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

    mgr = _task_manager(_project_dir())
    task = mgr.get_task(task_id)
    if task is None:
        console.print(f"[red]Error[/]: task {task_id} not found")
        sys.exit(1)

    plan_path = mgr.tasks_dir / task_id / "plan.md"
    if not plan_path.exists():
        console.print(f"[red]Error[/]: plan.md not found at {plan_path}")
        sys.exit(1)
    if mgr._is_empty_scaffold(task):
        console.print(
            f"[red]Plan is empty scaffold[/] — nothing to extract from {plan_path}"
        )
        sys.exit(2)

    plan_text = plan_path.read_text()
    candidates = extract_candidates(plan_text)
    if not candidates:
        console.print(
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
        console.print(f"[dim]Found {len(candidates)} candidate(s):[/]")
        for c in candidates:
            console.print(f"  [{c.kind}] line {c.line_no}: {c.title}")
        sys.exit(0)

    selected: list[tuple[Candidate, str]] = []  # (candidate, final_title)
    for c in candidates:
        if auto:
            selected.append((c, c.title))
            continue
        console.print(f"\n[{c.kind}] line {c.line_no}: [bold]{c.title}[/]")
        choice = click.prompt(
            "  [y]es / [n]o / [e]dit / [q]uit",
            default="n",
            show_default=False,
        ).strip().lower()
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
        console.print("Nothing extracted.")
        sys.exit(0)

    text = plan_text
    created: list[str] = []
    for cand, title in selected:
        try:
            child_id = mgr.next_id()
            mgr.create_task(
                task_id=child_id,
                title=title,
                priority="medium",
                tags=["extracted-from-plan"],
                parent_task=task_id,
            )
        except Exception as exc:  # pragma: no cover — defensive only
            console.print(f"[red]Failed to create task for[/] {title!r}: {exc}")
            continue
        text = mark_extracted(text, cand.line_no, child_id)
        created.append(f"{child_id}: {title}")

    if created:
        plan_path.write_text(text)
        console.print(f"\n[green]Created {len(created)} subtask(s):[/]")
        for line in created:
            console.print(f"  {line}")


@task.command("update")
@click.argument("task_id")
@click.option("--title", default=None, help="New title")
@click.option("--description", "-d", default=None, help="New description")
@click.option(
    "--priority", "-p", default=None, type=click.Choice(["low", "medium", "high"]), help="Priority"
)
@click.option("--resolution", default=None, help="Resolution note (why closed)")
@click.option("--role", default=None, help="Assigned role")
@click.option("--reviewer", default=None, help="Reviewer")
@click.option("--add-tag", multiple=True, help="Add tag")
@click.option("--remove-tag", multiple=True, help="Remove tag")
@click.option(
    "--parent-task", "parent_task", default=None,
    help="Set parent task ID (composition / epic→child relationship)",
)
@click.option(
    "--clear-parent", is_flag=True,
    help="Clear the parent task reference",
)
@click.option(
    "--add-depends", multiple=True,
    help="Add a blocker task ID (this task is blocked until that one is done)",
)
@click.option(
    "--remove-depends", multiple=True,
    help="Remove a blocker task ID",
)
def task_update(
    task_id: str,
    title: str | None,
    description: str | None,
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

    mgr = _task_manager(_project_dir())

    if clear_parent and parent_task is not None:
        console.print("[red]Error[/]: --clear-parent and --parent-task are mutually exclusive")
        sys.exit(1)
    parent_arg = "" if clear_parent else parent_task

    has_changes = any(
        [title, description, priority, resolution, role, reviewer,
         add_tag, remove_tag, parent_arg is not None, add_depends, remove_depends]
    )
    if not has_changes:
        console.print(
            "[yellow]No changes specified[/]. Use --title, --priority, --description, etc."
        )
        return

    try:
        t = mgr.update_task(
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
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    _warn_missing_refs(mgr, parent_arg or "", list(add_depends))
    console.print(f"[green]Updated[/]: {t.id} — {t.title} [{t.priority}]")


@task.command("log")
@click.argument("task_id")
@click.argument("message")
@click.option("--session", default=None, help="Session ID (defaults to AI_HATS_SESSION_ID)")
def task_log(task_id: str, message: str, session: str | None):
    """Log work progress on a task."""

    mgr = _task_manager(_project_dir())
    try:
        t = mgr.log_work(task_id, message, session_id=session or "")
        console.print(f"[green]Logged[/]: {t.id} — {message}")
    except ValueError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)


@task.command("list")
@click.option("--state", default=None, help="Filter by state")
@click.option("--priority", default=None, help="Filter by priority (low/medium/high)")
@click.option("--all", "-a", "show_all", is_flag=True, help="Include done/failed tasks")
@click.option("--search", "-s", default=None, help="Regex search across id, title, description, tags, parent_task, depends_on")
def task_list(state: str | None, priority: str | None, show_all: bool, search: str | None):
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

    mgr = _task_manager(_project_dir())
    filter_state = TaskState(state) if state else None
    tasks = mgr.list_tasks(state=filter_state, priority=priority)

    if not show_all and filter_state is None:
        tasks = [t for t in tasks if t.state not in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)]

    if search:
        try:
            pattern = _re.compile(search, _re.IGNORECASE)
        except _re.error as e:
            console.print(f"[red]Bad regex[/]: {e}")
            sys.exit(1)
        tasks = [
            t for t in tasks
            if pattern.search(
                "\n".join([
                    t.id, t.title, t.description, t.parent_task,
                    *t.tags, *t.depends_on, *t.related, *t.see_also,
                    t.folded_into,
                ])
            )
        ]

    if not tasks:
        console.print("[dim]No tasks[/]")
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

    console.print(table)


@task.command("show")
@click.argument("task_id")
def task_show(task_id: str):
    """Show task card details."""

    mgr = _task_manager(_project_dir())
    t = mgr.get_task(task_id)
    if t is None:
        console.print(f"[red]Task not found[/]: {task_id}")
        sys.exit(1)
    for k, v in t.to_dict().items():
        if v:
            console.print(f"  {k}: {v}")
    # Resolve depends_on into "Blocked by:" with each blocker's current state.
    # The raw `depends_on: [PROJ-X, PROJ-Y]` line above is opaque on its own —
    # this section answers "is this task actually unblocked yet?".
    if t.depends_on:
        console.print("\n  [bold]Blocked by:[/]")
        for dep_id in t.depends_on:
            dep = mgr.get_task(dep_id)
            if dep is None:
                console.print(f"    {dep_id} [red](missing)[/]")
            else:
                # Use parens around state — rich interprets `[brainstorm]` as a
                # malformed markup tag and silently drops it.
                console.print(f"    {dep_id} ({dep.state.value}) — {dep.title}")

    # Link sections — render each outbound relation with the target's state
    # so the cross-reference answers "is this still relevant?" at a glance.
    def _render_links(label: str, ids: list[str]) -> None:
        console.print(f"\n  [bold]{label}:[/]")
        for ref_id in ids:
            other = mgr.get_task(ref_id)
            if other is None:
                console.print(f"    {ref_id} [red](missing)[/]")
            else:
                console.print(
                    f"    {ref_id} ({other.state.value}) — {other.title}"
                )

    if t.related:
        _render_links("Related", t.related)
    if t.see_also:
        _render_links("See also", t.see_also)
    if t.folded_into:
        target = mgr.get_task(t.folded_into)
        console.print("\n  [bold]Folded into:[/]")
        if target is None:
            console.print(f"    {t.folded_into} [red](missing)[/]")
        else:
            console.print(
                f"    {t.folded_into} ({target.state.value}) — {target.title}"
            )
    # Inbound "Subsumed:" — scan all cards for folded_into pointing here.
    subsumed = mgr.find_subsumed_by(task_id)
    if subsumed:
        _render_links("Subsumed", subsumed)

    # Show work log nicely
    if t.work_log:
        console.print("\n  [bold]Work Log:[/]")
        for entry in t.work_log:
            console.print(f"    {entry.timestamp} — {entry.message}")


@task.command("sync")
def task_sync():
    """Synchronize STATE.md with task cards."""

    mgr = _task_manager(_project_dir())
    count = mgr.sync()
    console.print(f"[green]Synced[/]: {count} tasks")
