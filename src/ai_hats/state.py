"""State management — task state machine, shared state, file locking."""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from .models import TaskCard, TaskState

logger = logging.getLogger(__name__)


PLAN_SCAFFOLD = """\
# Plan for {task_id}: {title}

## Objective


## Architecture Changes


## Steps
- [ ] Step 1


## Verification Protocol

"""


class PlanSyncAmbiguousError(Exception):
    """Multiple .claude/plans/ candidates matched the same task ID."""

    def __init__(self, task_id: str, matches: list[Path]) -> None:
        super().__init__(f"Multiple plan candidates for {task_id}")
        self.task_id = task_id
        self.matches = matches


class EmptyPlanError(Exception):
    """transition → execute is blocked because plan.md is the empty scaffold."""

    def __init__(self, task_id: str, plan_path: Path) -> None:
        super().__init__(f"Plan is empty scaffold for {task_id}")
        self.task_id = task_id
        self.plan_path = plan_path


class TaskManager:
    """Manages task cards and state transitions with file-lock protection."""

    def __init__(
        self,
        project_dir: Path,
        prefix: str = "HATS",
        *,
        strict_plan_check: bool = True,
    ) -> None:
        from .paths import state_md_path, tasks_dir

        self.project_dir = project_dir
        self.tasks_dir = tasks_dir(project_dir)
        self.state_md_path = state_md_path(project_dir)
        self.strict_plan_check = strict_plan_check
        # Legacy index — removed after unification on STATE.md. Path retained
        # only to clean up stale files left from prior versions on first sync.
        self._legacy_backlog_md_path = project_dir / ".agent" / "backlog.md"
        self.prefix = prefix
        # Note: tasks_dir is created lazily on first write
        # (create_task / transition / log_work / update_task).
        # Eager mkdir here historically materialized stray DBs whenever a
        # caller resolved project_dir from the wrong directory — see HATS-197.

    def next_id(self) -> str:
        """Generate the next sequential task ID."""
        max_num = 0
        if self.tasks_dir.exists():
            for d in self.tasks_dir.iterdir():
                if d.is_dir():
                    match = re.search(rf"{self.prefix}-(\d+)", d.name)
                    if match:
                        max_num = max(max_num, int(match.group(1)))
        return f"{self.prefix}-{max_num + 1:03d}"

    def create_task(
        self,
        task_id: str,
        title: str,
        description: str = "",
        priority: str = "medium",
        role: str = "",
        reviewer: str = "user",
        parent_task: str = "",
        depends_on: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> TaskCard:
        """Create a new task card.

        ``parent_task`` and ``depends_on`` are validated for self-reference
        and (for depends_on) immediate A↔B cycles. Missing references are
        accepted silently at the manager level — surface warnings at the
        CLI edge via :meth:`missing_refs` so write paths remain pure.
        """
        depends = list(depends_on or [])
        self._reject_self_or_cycle(task_id, parent_task, depends)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        task = TaskCard(
            id=task_id,
            title=title,
            state=TaskState.BRAINSTORM,
            description=description,
            priority=priority,
            role=role,
            reviewer=reviewer,
            parent_task=parent_task,
            depends_on=depends,
            tags=tags or [],
            created=now,
            updated=now,
        )
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self._save_task(task)
        self._update_indexes()
        return task

    def missing_refs(self, ids: list[str]) -> list[str]:
        """Return the subset of ``ids`` that do not exist as task cards.

        Pure read — never raises. CLI uses this to print yellow warnings
        without blocking the write (typos and forward-references are common
        and should not be fatal).
        """
        return [i for i in ids if not (self.tasks_dir / i / "task.yaml").exists()]

    def _reject_self_or_cycle(
        self,
        task_id: str,
        parent: str,
        depends: list[str],
    ) -> None:
        """Block self-references and immediate A↔B depends cycles.

        Deeper transitive cycles (A→B→C→A) are out of scope — solving them
        properly needs a graph traversal that's a larger feature.
        """
        if parent == task_id:
            raise ValueError(f"Task '{task_id}' cannot be its own parent")
        if task_id in depends:
            raise ValueError(f"Task '{task_id}' cannot depend on itself")
        for dep_id in depends:
            dep = self.get_task(dep_id)
            if dep is not None and task_id in dep.depends_on:
                raise ValueError(
                    f"Cycle: '{task_id}' depends on '{dep_id}', but "
                    f"'{dep_id}' already depends on '{task_id}'"
                )

    def get_task(self, task_id: str) -> TaskCard | None:
        """Load a task card by ID."""
        task_file = self.tasks_dir / task_id / "task.yaml"
        if not task_file.exists():
            return None
        return TaskCard.from_yaml(task_file)

    def transition(
        self,
        task_id: str,
        new_state: TaskState,
        resolution: str | None = None,
    ) -> TaskCard:
        """Transition a task to a new state with file-lock protection.

        ``resolution`` is written atomically alongside the state change so
        cancellations record their reason in the same lock window. The CLI
        enforces that ``resolution`` is provided when ``new_state`` is
        CANCELLED; the manager itself is permissive (policy stays at the
        edge, not duplicated here).
        """
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            old_state = task.state
            task.transition_to(new_state)
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if resolution is not None:
                task.resolution = resolution

            # State-specific side effects
            if new_state == TaskState.PLAN:
                self._create_plan_scaffold(task)
                self._sync_plan_from_claude_plans(task)
            elif new_state == TaskState.EXECUTE:
                # Reopen path (HATS-328): coming back from DONE — clear the
                # completion timestamp and record the reopen in work_log so the
                # lifecycle stays auditable. Skip the empty-plan strict-check:
                # the plan already passed it once on the original execute.
                if old_state == TaskState.DONE:
                    task.completed_at = ""
                    task.log_work("Reopened from done")
                elif self.strict_plan_check and self._is_empty_scaffold(task):
                    plan_path = self.tasks_dir / task.id / "plan.md"
                    raise EmptyPlanError(task.id, plan_path)
                self._setup_worktree(task)
            elif new_state == TaskState.DONE:
                task.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._teardown_worktree(task, merge=True)
            elif new_state == TaskState.FAILED:
                self._teardown_worktree(task, merge=False)
            elif new_state == TaskState.CANCELLED:
                # Administrative close: stamp completion time and discard any
                # in-flight worktree (work isn't being kept).
                task.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._teardown_worktree(task, merge=False)

            self._save_task(task)
            self._update_indexes()

        return task

    def log_work(self, task_id: str, message: str, session_id: str = "") -> TaskCard:
        """Append a work log entry to a task."""
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if not session_id:
                session_id = os.environ.get("AI_HATS_SESSION_ID", "")

            task.log_work(message, session_id=session_id)
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(task)

        return task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        resolution: str | None = None,
        role: str | None = None,
        reviewer: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        parent_task: str | None = None,
        add_depends: list[str] | None = None,
        remove_depends: list[str] | None = None,
    ) -> TaskCard:
        """Update task card fields.

        ``parent_task=""`` clears the parent. Pass ``None`` to leave it
        untouched. ``add_depends`` / ``remove_depends`` mutate the list
        the same way ``add_tags`` / ``remove_tags`` do.
        """
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            if priority is not None:
                task.priority = priority
            if resolution is not None:
                task.resolution = resolution
            if role is not None:
                task.role = role
            if reviewer is not None:
                task.reviewer = reviewer
            if add_tags:
                for tag in add_tags:
                    if tag not in task.tags:
                        task.tags.append(tag)
            if remove_tags:
                task.tags = [t for t in task.tags if t not in remove_tags]
            if parent_task is not None:
                task.parent_task = parent_task
            if add_depends:
                for dep in add_depends:
                    if dep not in task.depends_on:
                        task.depends_on.append(dep)
            if remove_depends:
                task.depends_on = [d for d in task.depends_on if d not in remove_depends]

            # Validate AFTER mutation so add+remove in the same call resolves first.
            self._reject_self_or_cycle(task_id, task.parent_task, task.depends_on)

            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(task)
            self._update_indexes()

        return task

    def set_final_state(self, task_id: str, final_state: str) -> TaskCard:
        """Record the final accomplished state before review."""
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")
            task.final_state = final_state
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(task)

        return task

    def list_tasks(
        self,
        state: TaskState | None = None,
        priority: str | None = None,
    ) -> list[TaskCard]:
        """List all tasks, optionally filtered by state and/or priority."""
        tasks = []
        if not self.tasks_dir.exists():
            return tasks
        for task_dir in sorted(self.tasks_dir.iterdir()):
            task_file = task_dir / "task.yaml"
            if task_file.exists():
                task = TaskCard.from_yaml(task_file)
                if state is not None and task.state != state:
                    continue
                if priority is not None and task.priority != priority:
                    continue
                tasks.append(task)
        return tasks

    def sync(self) -> int:
        """Synchronize STATE.md with current task cards. Returns task count."""
        headers = self._iter_headers()
        self._update_state_md(headers)
        if self._legacy_backlog_md_path.exists():
            self._legacy_backlog_md_path.unlink()
        return len(headers)

    # -- Internal --

    def _save_task(self, task: TaskCard) -> None:
        task_file = self.tasks_dir / task.id / "task.yaml"
        task.save(task_file)

    def _setup_worktree(self, task: TaskCard) -> Path | None:
        """Create or adopt an isolated worktree when task enters execute state.

        HATS-061: each task gets its own worktree state slot — no singleton
        conflict between parallel tasks.

        Returns the adopted linked-worktree path if invoked from inside one
        (HATS-060 short-circuit), the existing/created worktree path on the
        happy path, or None for non-git projects.
        """
        from .worktree import WorktreeManager

        # HATS-060: invoked from inside a linked worktree → adopt it.
        if WorktreeManager.is_inside_linked_worktree(self.project_dir):
            return self.project_dir

        # Per-task lookup (HATS-061) — each task has its own state slot.
        existing = WorktreeManager.load_for_task(self.project_dir, task.id)
        if existing is not None:
            return existing.worktree_path

        # No existing worktree for this task — create one.
        branch = f"task/{task.id.lower()}"
        mgr = WorktreeManager(self.project_dir, branch_name=branch)
        path = mgr.create()
        if path != self.project_dir:  # git repo — worktree created
            mgr.save_state()
            return path
        return None

    def _teardown_worktree(self, task: TaskCard, *, merge: bool = True) -> None:
        """Merge or discard the worktree for a specific task (HATS-061)."""
        from .worktree import OriginalBranchMissingError, WorktreeManager

        active = WorktreeManager.load_for_task(self.project_dir, task.id)
        if active is None:
            return

        try:
            if merge:
                active.merge()
            else:
                active.discard(force=True)  # failed → intentional discard
        except OriginalBranchMissingError as exc:
            logger.warning("Worktree merge skipped: %s", exc)
        except Exception:
            logger.warning(
                "Worktree %s failed, branch '%s' preserved",
                "merge" if merge else "discard",
                active.branch_name,
                exc_info=True,
            )

    def _create_plan_scaffold(self, task: TaskCard) -> None:
        """Create plan.md scaffold when task moves to plan state."""
        plan_path = self.tasks_dir / task.id / "plan.md"
        if not plan_path.exists():
            plan_path.write_text(
                PLAN_SCAFFOLD.format(task_id=task.id, title=task.title)
            )

    def find_claude_plan_for_task(self, task_id: str) -> list[Path]:
        """Glob `<project>/.claude/plans/` for files matching the task ID.

        Matches both bare-number (`230-*.md`) and prefixed (`hats-230-*.md`)
        conventions, case-insensitive on the prefix.
        """
        plans_dir = self.project_dir / ".claude" / "plans"
        if not plans_dir.is_dir():
            return []
        m = re.search(r"(\d+)$", task_id)
        if not m:
            return []
        nn = m.group(1)
        prefix = task_id[: -len(nn)].rstrip("-_").lower()
        seen: set[Path] = set()
        candidates: list[Path] = []
        for pat in [f"{nn}-*.md", f"{prefix}-{nn}-*.md"] if prefix else [f"{nn}-*.md"]:
            for p in plans_dir.glob(pat):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    candidates.append(p)
        return sorted(candidates)

    def is_empty_scaffold_for_id(self, task_id: str) -> bool:
        """Public-friendly variant: load card and delegate to _is_empty_scaffold."""
        task = self.get_task(task_id)
        if task is None:
            return False
        return self._is_empty_scaffold(task)

    def _is_empty_scaffold(self, task: TaskCard) -> bool:
        plan_path = self.tasks_dir / task.id / "plan.md"
        if not plan_path.exists():
            return True
        expected = PLAN_SCAFFOLD.format(task_id=task.id, title=task.title)
        try:
            return plan_path.read_text() == expected
        except OSError:
            return False

    def _sync_plan_from_claude_plans(self, task: TaskCard) -> Path | None:
        """At transition→plan: move .claude/plans/<NN>-*.md into task tree.

        - 0 matches: leave the freshly created scaffold in place.
        - 1 match: shutil.move() over the scaffold.
        - >1 matches: raise PlanSyncAmbiguousError; CLI prints the list.
        """
        matches = self.find_claude_plan_for_task(task.id)
        if not matches:
            return None
        if len(matches) > 1:
            raise PlanSyncAmbiguousError(task.id, matches)
        src = matches[0]
        dst = self.tasks_dir / task.id / "plan.md"
        shutil.move(str(src), str(dst))
        return src

    def _iter_headers(self) -> list[dict[str, str]]:
        """Lightweight scan for index rendering — bypasses full YAML parse.

        Returns one header dict per task.yaml. ~60× faster than list_tasks()
        on large cards because work_log/description/acceptance_criteria are
        never decoded. See TaskCard.load_header for the regex contract and
        full-parse fallback.
        """
        headers: list[dict[str, str]] = []
        if not self.tasks_dir.exists():
            return headers
        for task_dir in sorted(self.tasks_dir.iterdir()):
            task_file = task_dir / "task.yaml"
            if task_file.exists():
                headers.append(TaskCard.load_header(task_file))
        return headers

    def _update_indexes(self) -> None:
        """Regenerate STATE.md (single source of truth for the task index)."""
        headers = self._iter_headers()
        self._update_state_md(headers)
        if self._legacy_backlog_md_path.exists():
            self._legacy_backlog_md_path.unlink()

    def _update_state_md(self, headers: list[dict[str, str]]) -> None:
        """Regenerate STATE.md from header dicts."""
        lines = ["# Task State\n"]

        by_state: dict[str, list[dict[str, str]]] = {}
        for h in headers:
            by_state.setdefault(h["state"], []).append(h)

        state_order = ["execute", "document", "plan", "brainstorm", "review", "blocked", "failed", "done", "cancelled"]
        for state_name in state_order:
            state_tasks = by_state.get(state_name, [])
            if state_tasks:
                lines.append(f"\n## {state_name.upper()}\n")
                for h in state_tasks:
                    line = f"- **{h['id']}**: {h['title']}"
                    if h["priority"] != "medium":
                        line += f" [{h['priority']}]"
                    if h["assignee"]:
                        line += f" (@{h['assignee']})"
                    if h["role"]:
                        line += f" [role: {h['role']}]"
                    lines.append(line)

        if not headers:
            lines.append("\nNo active tasks.\n")

        self.state_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_md_path.write_text("\n".join(lines) + "\n")
