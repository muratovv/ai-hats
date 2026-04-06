"""State management — task state machine, shared state, file locking."""

from __future__ import annotations

import logging
import os
import re
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


class TaskManager:
    """Manages task cards and state transitions with file-lock protection."""

    def __init__(self, project_dir: Path, prefix: str = "HATS") -> None:
        self.project_dir = project_dir
        self.tasks_dir = project_dir / ".agent" / "backlog" / "tasks"
        self.state_md_path = project_dir / ".agent" / "STATE.md"
        self.backlog_md_path = project_dir / ".agent" / "backlog.md"
        self.prefix = prefix
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

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
        tags: list[str] | None = None,
    ) -> TaskCard:
        """Create a new task card."""
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
            tags=tags or [],
            created=now,
            updated=now,
        )
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self._save_task(task)
        self._update_indexes()
        return task

    def get_task(self, task_id: str) -> TaskCard | None:
        """Load a task card by ID."""
        task_file = self.tasks_dir / task_id / "task.yaml"
        if not task_file.exists():
            return None
        return TaskCard.from_yaml(task_file)

    def transition(self, task_id: str, new_state: TaskState) -> TaskCard:
        """Transition a task to a new state with file-lock protection."""
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            task.transition_to(new_state)
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # State-specific side effects
            if new_state == TaskState.PLAN:
                self._create_plan_scaffold(task)
            elif new_state == TaskState.EXECUTE:
                self._setup_worktree(task)
            elif new_state == TaskState.DONE:
                task.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._teardown_worktree(merge=True)
            elif new_state == TaskState.FAILED:
                self._teardown_worktree(merge=False)

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
        """Synchronize indexes with current task cards. Returns task count."""
        self._update_indexes()
        return len(self.list_tasks())

    # -- Internal --

    def _save_task(self, task: TaskCard) -> None:
        task_file = self.tasks_dir / task.id / "task.yaml"
        task.save(task_file)

    def _setup_worktree(self, task: TaskCard) -> Path | None:
        """Create isolated worktree when task enters execute state."""
        from .worktree import WorktreeManager

        active = WorktreeManager.load_active(self.project_dir)
        if active is not None:
            # Check if it belongs to this task (reuse after blocked → execute)
            expected_branch = f"task/{task.id.lower()}"
            if active.branch_name == expected_branch:
                return active.worktree_path
            raise ValueError(
                f"Active worktree exists on branch '{active.branch_name}'. "
                f"Merge or discard first: ai-hats wt merge / ai-hats wt discard"
            )

        branch = f"task/{task.id.lower()}"
        mgr = WorktreeManager(self.project_dir, branch_name=branch)
        path = mgr.create()
        if path != self.project_dir:  # git repo — worktree created
            mgr.save_state()
            return path
        return None

    def _teardown_worktree(self, *, merge: bool = True) -> None:
        """Merge or discard active worktree on task completion/failure."""
        from .worktree import WorktreeManager

        active = WorktreeManager.load_active(self.project_dir)
        if active is None:
            return

        try:
            if merge:
                active.merge(squash=True)
            else:
                active.discard()
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

    def _update_indexes(self) -> None:
        """Regenerate both STATE.md and backlog.md."""
        self._update_state_md()
        self._update_backlog_md()

    def _update_state_md(self) -> None:
        """Regenerate STATE.md from current task cards."""
        tasks = self.list_tasks()
        lines = ["# Task State\n"]

        by_state: dict[str, list[TaskCard]] = {}
        for task in tasks:
            by_state.setdefault(task.state.value, []).append(task)

        state_order = ["execute", "document", "plan", "brainstorm", "review", "blocked", "failed", "done"]
        for state_name in state_order:
            state_tasks = by_state.get(state_name, [])
            if state_tasks:
                lines.append(f"\n## {state_name.upper()}\n")
                for t in state_tasks:
                    line = f"- **{t.id}**: {t.title}"
                    if t.priority != "medium":
                        line += f" [{t.priority}]"
                    if t.assignee:
                        line += f" (@{t.assignee})"
                    if t.role:
                        line += f" [role: {t.role}]"
                    lines.append(line)

        if not tasks:
            lines.append("\nNo active tasks.\n")

        self.state_md_path.write_text("\n".join(lines) + "\n")

    def _update_backlog_md(self) -> None:
        """Regenerate backlog.md — tabular index of all tasks."""
        tasks = self.list_tasks()
        lines = [
            "# Project Backlog\n",
            "| ID | Title | Priority | State | Reviewer |",
            "|----|-------|----------|-------|----------|",
        ]

        for t in tasks:
            title_short = t.title[:50] + "..." if len(t.title) > 50 else t.title
            lines.append(
                f"| {t.id} | {title_short} | {t.priority} | {t.state.value} | {t.reviewer} |"
            )

        self.backlog_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.backlog_md_path.write_text("\n".join(lines) + "\n")
