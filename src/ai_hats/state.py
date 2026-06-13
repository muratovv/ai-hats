"""State management — task state machine, shared state, file locking."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock

from .models import TaskCard, TaskState

if TYPE_CHECKING:
    # Annotation-only import for attach_add's return type. A runtime import
    # would risk a circular dependency (attachments → state); the
    # TYPE_CHECKING guard keeps the name available to type-checkers / ruff
    # (resolves F821) without importing at module load.
    from .attachments import ReconcileResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Section:
    """One section of the plan template.

    `required=True` sections are enforced by the plan→execute gate (see
    `TaskManager._unfilled_sections`). `required=False` sections (e.g. the
    "Approach & counter" value-counter stage added in M3 / HATS-621) are
    scaffolded and skill-routed but never block the transition — the
    "non-trivial plans must fill it or write explicit N/A" norm is
    behavioural (the `devils-advocate` skill + companion HYP), since the
    engine cannot judge triviality.
    """

    name: str
    required: bool = True


# Single source of truth for the plan template. The scaffold (what the agent
# starts from) and the gate (what `_unfilled_sections` enforces) both read
# THIS list, so contract and enforcement can never drift (HATS-635).
PLAN_SECTIONS: list[Section] = [
    Section(name="Requirements"),
    # Conditional value-counter stage (M3 / HATS-621): challenge the value
    # stated in Requirements *before* scoping. required=False → never blocks
    # the gate; routed by the `devils-advocate` plan-gate stage.
    Section(name="Approach & counter", required=False),
    Section(name="Scope & Out-of-scope"),
    Section(name="Steps"),
    Section(name="Verification Protocol"),
]


def render_plan_scaffold(sections: list[Section]) -> str:
    """Render the plan.md scaffold from the section schema.

    Each section is a level-2 heading with an EMPTY body. The empty body is
    deliberate: the per-section gate treats any pre-filled placeholder text as
    "filled", which would silently defeat the gate (HATS-635). The returned
    string still carries `{task_id}` / `{title}` placeholders for `.format()`.
    """
    parts = ["# Plan for {task_id}: {title}\n"]
    parts.extend(f"## {section.name}\n" for section in sections)
    return "\n".join(parts) + "\n"


PLAN_SCAFFOLD = render_plan_scaffold(PLAN_SECTIONS)


class EmptyPlanError(Exception):
    """transition → execute is blocked: required plan sections are empty.

    `empty_sections` names the required sections that lack body content, so
    callers (the CLI) can tell the user exactly what to fill (HATS-635).
    """

    def __init__(
        self,
        task_id: str,
        plan_path: Path,
        empty_sections: list[str] | None = None,
    ) -> None:
        self.empty_sections = empty_sections or []
        if self.empty_sections:
            joined = ", ".join(self.empty_sections)
            super().__init__(
                f"Plan for {task_id} has empty required section(s): {joined}"
            )
        else:
            super().__init__(f"Plan is empty scaffold for {task_id}")
        self.task_id = task_id
        self.plan_path = plan_path


@dataclass(frozen=True)
class TaskTransition:
    """A state change applied to a ticket as a side effect of a manager call.

    Returned alongside the primary card by the mutating methods
    (``transition`` / ``create_task`` / ``update_task`` / ``close_task``) so
    callers — the CLI — can surface harness-driven transitions the user did
    not ask for directly. Today the only producer is child-driven epic
    auto-advance / reopen (HATS-690 / Req 2 of HATS-688); the methods return a
    *list* of these so future non-epic propagations can be added without
    another signature change.
    """

    ticket: TaskCard
    from_state: TaskState
    to_state: TaskState
    reason: str


# Child states that count as "resolved" for epic auto-advance (HATS-690 Q2a).
# A child in any of these no longer blocks its epic; FAILED / BLOCKED are
# outstanding work and keep the epic open.
_EPIC_RESOLVED_STATES: frozenset[TaskState] = frozenset(
    {TaskState.DONE, TaskState.CANCELLED}
)

# Child states that count as "work taken" for epic activation (HATS-692 D2).
# A child in any of these means real work has started under the epic, so a
# `plan`-state epic should be activated to `execute`. BRAINSTORM / PLAN are
# pre-work; BLOCKED / FAILED are not "active progress"; DONE / CANCELLED are
# handled by the advance branch.
_EPIC_ACTIVE_STATES: frozenset[TaskState] = frozenset(
    {TaskState.EXECUTE, TaskState.DOCUMENT, TaskState.REVIEW}
)


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
    ) -> tuple[TaskCard, list[TaskTransition]]:
        """Create a new task card.

        ``parent_task`` and ``depends_on`` are validated for self-reference
        and (for depends_on) immediate A↔B cycles. Missing references are
        accepted silently at the manager level — surface warnings at the
        CLI edge via :meth:`missing_refs` so write paths remain pure.
        """
        depends = list(depends_on or [])
        self._reject_self_or_cycle(task_id, parent_task, depends)
        if (self.tasks_dir / task_id / "task.yaml").exists():
            raise ValueError(f"Task '{task_id}' already exists")
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
        # New work under a `done` epic auto-reopens it (HATS-690 Q3). create_task
        # has no lock window, so this post-write call honours the "post-lock" rule.
        return task, self._propagate_to_parent(task)

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
        *,
        force: bool = False,
        reason: str | None = None,
    ) -> tuple[TaskCard, list[TaskTransition]]:
        """Transition a task to a new state with file-lock protection.

        ``resolution`` is written atomically alongside the state change so
        cancellations record their reason in the same lock window. The CLI
        enforces that ``resolution`` is provided when ``new_state`` is
        CANCELLED; the manager itself is permissive (policy stays at the
        edge, not duplicated here).

        ``force=True`` bypasses the FSM guard for corrective transitions
        (e.g. ``plan → brainstorm`` when planning was started by mistake).
        ``reason`` is required when ``force`` is set and is recorded in
        ``work_log``. State-specific side effects (worktree setup/teardown,
        plan scaffold) still fire based on ``new_state`` — ``--force`` only
        relaxes the guard, not the post-transition machinery.
        """
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if force and not (reason and reason.strip()):
                raise ValueError("force=True requires a non-empty reason")

            old_state = task.state
            if force:
                if old_state == new_state:
                    raise ValueError(
                        f"Task '{task_id}' is already in state '{new_state.value}'"
                    )
                task.state = new_state
                task.log_work(
                    f"Forced transition {old_state.value} → {new_state.value}: {reason}"
                )
            else:
                task.transition_to(new_state)
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if resolution is not None:
                task.resolution = resolution

            # State-specific side effects
            if new_state == TaskState.PLAN:
                self._create_plan_scaffold(task)
            elif new_state == TaskState.EXECUTE:
                # Reopen path (HATS-328): coming back from DONE — clear the
                # completion timestamp and record the reopen in work_log so the
                # lifecycle stays auditable. Skip the empty-plan strict-check:
                # the plan already passed it once on the original execute.
                if old_state == TaskState.DONE:
                    task.completed_at = ""
                    task.log_work("Reopened from done")
                elif self.strict_plan_check:
                    unfilled = self._unfilled_sections(task)
                    if unfilled:
                        plan_path = self.tasks_dir / task.id / "plan.md"
                        raise EmptyPlanError(task.id, plan_path, unfilled)
                self._setup_worktree(task)
            elif new_state == TaskState.DONE:
                task.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                # HATS-596: plumb `force` so a corrective `transition done
                # --force` reaches the merge guards (e.g. bypass _check_clean
                # on an already-merged worktree). force does NOT relax the
                # HEAD-mismatch guard — that stays a correctness gate.
                self._teardown_worktree(task, merge=True, force=force)
            elif new_state == TaskState.FAILED:
                self._teardown_worktree(task, merge=False)
            elif new_state == TaskState.CANCELLED:
                # Administrative close: stamp completion time and discard any
                # in-flight worktree (work isn't being kept).
                task.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._teardown_worktree(task, merge=False)

            self._save_task(task)
            self._update_indexes()

        # Post-lock (no nested locks): a child reaching a terminal state may
        # complete its epic; a child reopened to execute may reopen a done
        # epic (HATS-690 Q2/Q3).
        return task, self._propagate_to_parent(task)

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
    ) -> tuple[TaskCard, list[TaskTransition]]:
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

        # Re-parenting a live task into a `done` epic reopens it (HATS-690 Q3).
        return task, self._propagate_to_parent(task)

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

    def close_task(
        self, task_id: str, resolution: str
    ) -> tuple[TaskCard, list[TaskTransition]]:
        """Fast-close: ``brainstorm | plan → done`` with mandatory resolution.

        Skips the worktree theatre — there is no worktree in brainstorm/plan,
        and the work was shipped on master out-of-band. Records the
        resolution and a work_log entry so the close stays auditable.

        Refuses to close from execute/document/review (those have real
        worktree state and should walk the regular ``transition done`` path)
        and from terminal states (done/failed/cancelled).
        """
        if not (resolution and resolution.strip()):
            raise ValueError("close_task requires a non-empty resolution")
        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if task.state not in (TaskState.BRAINSTORM, TaskState.PLAN):
                raise ValueError(
                    f"close is only valid from brainstorm or plan "
                    f"(current: {task.state.value}). "
                    "Use `task transition <id> done` from review, "
                    "or `--force` for corrective overrides."
                )

            old_state = task.state
            task.state = TaskState.DONE
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            task.completed_at = now
            task.updated = now
            task.resolution = resolution
            task.log_work(f"Fast-closed from {old_state.value}: {resolution}")
            self._save_task(task)
            self._update_indexes()

        # A child fast-closed to done can complete its epic (HATS-690 D2).
        return task, self._propagate_to_parent(task)

    # ----- Attachments (HATS-402) -----

    def attach_add(
        self,
        task_id: str,
        blob_path: Path,
        name: str,
        note: str = "",
    ) -> "ReconcileResult":
        """Attach ``blob_path`` to ``task_id`` under ``name``.

        Performs reconcile + file-op + manifest update + work_log entry under
        the task's file lock. On ERROR_COLLISION nothing is mutated.
        """
        from .attachments import (
            FileOp,
            ReconcileAction,
            attachments_dir,
            reconcile,
        )

        if not blob_path.is_file():
            raise ValueError(f"blob not found: {blob_path}")

        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            card_dir = self.tasks_dir / task_id
            result = reconcile(task, card_dir, blob_path, name=name, note=note)

            if result.action is ReconcileAction.ERROR_COLLISION:
                return result

            if result.action is ReconcileAction.NOOP:
                return result

            # ADDED or REGISTERED_EXISTING — both append to manifest; only
            # ADDED moves the file.
            assert result.attachment is not None
            if result.file_op is FileOp.MOVE:
                target_dir = attachments_dir(card_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(blob_path), str(target_dir / name))

            task.attachments.append(result.attachment)
            task.log_work(
                f"attached '{name}' (digest {result.attachment.digest}, "
                f"{result.action.value})"
            )
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(task)
            return result

    def attach_remove(
        self,
        task_id: str,
        name: str,
    ) -> "tuple[TaskCard, object | None, Path]":
        """Remove ``name`` from ``task_id`` (manifest + blob).

        Returns ``(card, removed_entry_or_None, blob_path)``. The blob_path
        is returned even on miss so the CLI layer can report it. Does
        nothing if the entry isn't on the card; the CLI surfaces that.
        """
        from .attachments import attachments_dir

        lock_path = self.tasks_dir / task_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_path)):
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            card_dir = self.tasks_dir / task_id
            blob_path = attachments_dir(card_dir) / name

            entry = next((a for a in task.attachments if a.name == name), None)
            if entry is None:
                return task, None, blob_path

            task.attachments = [a for a in task.attachments if a.name != name]
            if blob_path.is_file():
                # HATS-470: user-uploaded blob — route through trash so
                # accidental detach is recoverable.
                from .safe_delete import discard as _safe_discard
                _safe_discard(
                    blob_path, reason="attachment-detach",
                    project_dir=self.project_dir,
                )
            task.log_work(f"detached '{name}' (digest {entry.digest})")
            task.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(task)
            return task, entry, blob_path

    def attach_verify(self, task_id: str) -> "list":
        """Return divergences between ``task_id``'s manifest and its on-disk folder."""
        from .attachments import verify_manifest

        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task '{task_id}' not found")
        return verify_manifest(task, self.tasks_dir / task_id)

    # Link types accepted by ``add_link`` / ``remove_link``. Centralised so the
    # CLI choice list and the dispatch table stay in sync.
    LINK_TYPES: tuple[str, ...] = ("related", "see-also", "fold")

    def add_link(
        self,
        from_id: str,
        to_id: str,
        link_type: str = "related",
    ) -> tuple[TaskCard, TaskCard]:
        """Create a cross-reference between two task cards.

        - ``related`` / ``see-also``: symmetric — written to both cards.
        - ``fold``: directional — ``from_id`` is folded into ``to_id``.
          Sets ``from.folded_into = to_id``. The inverse "Subsumed by"
          relation is computed on read via :meth:`find_subsumed_by`.

        Refuses self-links and (for ``fold``) overwriting an existing
        ``folded_into`` — the caller should ``remove_link`` first.
        """
        if link_type not in self.LINK_TYPES:
            raise ValueError(
                f"Unknown link type '{link_type}'. "
                f"Valid: {list(self.LINK_TYPES)}"
            )
        if from_id == to_id:
            raise ValueError("Cannot link a task to itself")

        a = self.get_task(from_id)
        if a is None:
            raise ValueError(f"Task '{from_id}' not found")
        b = self.get_task(to_id)
        if b is None:
            raise ValueError(f"Task '{to_id}' not found")

        # Two-card writes don't share a single lock — we take both serially,
        # smaller-ID-first to avoid deadlocks under concurrent link/unlink.
        ids_sorted = sorted([from_id, to_id])
        lock_a = self.tasks_dir / ids_sorted[0] / ".lock"
        lock_b = self.tasks_dir / ids_sorted[1] / ".lock"
        lock_a.parent.mkdir(parents=True, exist_ok=True)
        lock_b.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_a)), FileLock(str(lock_b)):
            a = self.get_task(from_id)
            b = self.get_task(to_id)
            if a is None or b is None:
                raise ValueError("Task disappeared during link")

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if link_type == "fold":
                if a.folded_into and a.folded_into != to_id:
                    raise ValueError(
                        f"'{from_id}' is already folded into "
                        f"'{a.folded_into}'. Remove that link first."
                    )
                a.folded_into = to_id
                a.updated = now
                self._save_task(a)
            elif link_type == "related":
                changed = False
                if to_id not in a.related:
                    a.related.append(to_id)
                    a.updated = now
                    changed = True
                if from_id not in b.related:
                    b.related.append(from_id)
                    b.updated = now
                    changed = True
                if changed:
                    self._save_task(a)
                    self._save_task(b)
            else:  # see-also
                changed = False
                if to_id not in a.see_also:
                    a.see_also.append(to_id)
                    a.updated = now
                    changed = True
                if from_id not in b.see_also:
                    b.see_also.append(from_id)
                    b.updated = now
                    changed = True
                if changed:
                    self._save_task(a)
                    self._save_task(b)

            self._update_indexes()
            return a, b

    def remove_link(
        self,
        from_id: str,
        to_id: str,
        link_type: str = "related",
    ) -> tuple[TaskCard, TaskCard]:
        """Inverse of :meth:`add_link`. Silently no-ops if the link is absent."""
        if link_type not in self.LINK_TYPES:
            raise ValueError(
                f"Unknown link type '{link_type}'. "
                f"Valid: {list(self.LINK_TYPES)}"
            )
        a = self.get_task(from_id)
        if a is None:
            raise ValueError(f"Task '{from_id}' not found")
        b = self.get_task(to_id)
        if b is None:
            raise ValueError(f"Task '{to_id}' not found")

        ids_sorted = sorted([from_id, to_id])
        lock_a = self.tasks_dir / ids_sorted[0] / ".lock"
        lock_b = self.tasks_dir / ids_sorted[1] / ".lock"
        lock_a.parent.mkdir(parents=True, exist_ok=True)
        lock_b.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(str(lock_a)), FileLock(str(lock_b)):
            a = self.get_task(from_id)
            b = self.get_task(to_id)
            if a is None or b is None:
                raise ValueError("Task disappeared during unlink")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if link_type == "fold":
                if a.folded_into == to_id:
                    a.folded_into = ""
                    a.updated = now
                    self._save_task(a)
            elif link_type == "related":
                changed = False
                if to_id in a.related:
                    a.related = [x for x in a.related if x != to_id]
                    a.updated = now
                    changed = True
                if from_id in b.related:
                    b.related = [x for x in b.related if x != from_id]
                    b.updated = now
                    changed = True
                if changed:
                    self._save_task(a)
                    self._save_task(b)
            else:  # see-also
                changed = False
                if to_id in a.see_also:
                    a.see_also = [x for x in a.see_also if x != to_id]
                    a.updated = now
                    changed = True
                if from_id in b.see_also:
                    b.see_also = [x for x in b.see_also if x != from_id]
                    b.updated = now
                    changed = True
                if changed:
                    self._save_task(a)
                    self._save_task(b)

            self._update_indexes()
            return a, b

    def find_subsumed_by(self, task_id: str) -> list[str]:
        """Return IDs of tasks whose ``folded_into`` points at ``task_id``.

        Cheap regex scan over the task files — avoids a full YAML parse for
        every card on a ``task show`` render. Falls back to a full load if
        the regex misses (e.g. quoted scalar layout).
        """
        if not self.tasks_dir.exists():
            return []
        pattern = re.compile(
            rf"^folded_into:\s*['\"]?{re.escape(task_id)}['\"]?\s*$",
            re.MULTILINE,
        )
        subsumed: list[str] = []
        for task_dir in sorted(self.tasks_dir.iterdir()):
            task_file = task_dir / "task.yaml"
            if not task_file.exists():
                continue
            try:
                text = task_file.read_text()
            except OSError:
                continue
            if pattern.search(text):
                # Read the id from the same file (don't trust dir name —
                # users may rename dirs).
                m = re.search(r"^id:\s*['\"]?([^'\"\n]+)['\"]?\s*$", text, re.MULTILINE)
                if m:
                    subsumed.append(m.group(1).strip())
        return subsumed

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
        from .safe_delete import discard as _safe_discard

        headers = self._iter_headers()
        self._update_state_md(headers)
        if self._legacy_backlog_md_path.exists():
            _safe_discard(
                self._legacy_backlog_md_path,
                reason="legacy-backlog",
                project_dir=self.project_dir,
            )
        return len(headers)

    # -- Internal --

    def _save_task(self, task: TaskCard) -> None:
        task_file = self.tasks_dir / task.id / "task.yaml"
        task.save(task_file)

    def _children_of(self, epic_id: str) -> list[TaskCard]:
        """Return the task cards whose ``parent_task`` is ``epic_id``.

        Regex-prefilters the on-disk cards (mirrors :meth:`find_subsumed_by`)
        so only cards that actually name ``epic_id`` as their parent are fully
        parsed — the reverse ``parent_task`` scan, since ``subtasks`` is a dead
        field (``models.py`` HATS-688 note).
        """
        if not self.tasks_dir.exists():
            return []
        pattern = re.compile(
            rf"^parent_task:\s*['\"]?{re.escape(epic_id)}['\"]?\s*$",
            re.MULTILINE,
        )
        children: list[TaskCard] = []
        for task_dir in sorted(self.tasks_dir.iterdir()):
            task_file = task_dir / "task.yaml"
            if not task_file.exists():
                continue
            try:
                text = task_file.read_text()
            except OSError:
                continue
            if pattern.search(text):
                try:
                    children.append(TaskCard.from_yaml(task_file))
                except (OSError, ValueError):
                    continue
        return children

    def _propagate_to_parent(self, child: TaskCard) -> list[TaskTransition]:
        """Child-driven epic auto-transition (HATS-690 + HATS-692).

        Called *after* the child's own lock window closes (no nested locks).
        Takes the parent epic's lock and, based on the child set, applies one of
        the three epic-sync transitions:

        * **reopen** — a ``done`` epic with a live (not ``{done, cancelled}``)
          just-mutated child ⟹ ``done → execute`` (HATS-690 Q3); or
        * **advance** — every child resolved (``{done, cancelled}``) with ≥1
          ``done`` and the epic in ``plan`` / ``execute`` / ``document`` ⟹
          ``→ review`` via FSM-valid hops. The ``plan`` source is the HATS-692
          advance-fallback: a planned epic whose children all closed without
          ever entering ``execute`` (e.g. ``close_task`` fast-close) still
          advances; or
        * **activate** — a ``plan`` epic whose just-mutated child became active
          (``{execute, document, review}``) ⟹ ``plan → execute`` (HATS-692);
          ``brainstorm`` epics are left alone (not yet decomposed); or
        * **no-ops**.

        The epic is mutated *directly* (FSM-validated hops via
        :meth:`TaskCard.transition_to`) rather than through the public
        :meth:`transition`: epics never get a worktree in any auto-path, and
        grandparent cascade stays structurally impossible (no recursion).
        Returns a list (empty, or one delta) so future propagations can extend it.
        """
        epic_id = child.parent_task
        if not epic_id:
            return []
        if not (self.tasks_dir / epic_id / "task.yaml").exists():
            return []

        lock_path = self.tasks_dir / epic_id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path)):
            epic = self.get_task(epic_id)
            if epic is None:
                return []

            from_state = epic.state
            reason: str | None = None

            if (
                epic.state == TaskState.DONE
                and child.state not in _EPIC_RESOLVED_STATES
            ):
                # Reopen: live child work under a completed epic (Q3).
                epic.transition_to(TaskState.EXECUTE)  # DONE → EXECUTE (FSM-valid)
                epic.completed_at = ""
                reason = f"reopened: live child {child.id} ({child.state.value})"
                epic.log_work(f"Auto-reopened done → execute ({reason})")
                # No _setup_worktree — auto-reopened epics get no worktree.
            elif epic.state in (
                TaskState.PLAN,
                TaskState.EXECUTE,
                TaskState.DOCUMENT,
            ):
                # Advance: all children resolved, >=1 done (Q2 / Q2a). The PLAN
                # source is the HATS-692 fallback for the fast-close path.
                children = self._children_of(epic_id)
                resolved = bool(children) and all(
                    c.state in _EPIC_RESOLVED_STATES for c in children
                )
                has_done = any(c.state == TaskState.DONE for c in children)
                if resolved and has_done:
                    # Multi-hop to review via FSM-valid hops from the current
                    # state (review is only reachable from document). Chained
                    # `if`s let plan -> execute -> document -> review cascade.
                    if epic.state == TaskState.PLAN:
                        epic.transition_to(TaskState.EXECUTE)
                    if epic.state == TaskState.EXECUTE:
                        epic.transition_to(TaskState.DOCUMENT)
                    epic.transition_to(TaskState.REVIEW)
                    reason = "all children resolved (>=1 done)"
                    epic.log_work(
                        f"Auto-advanced {from_state.value} -> review ({reason})"
                    )
                elif (
                    epic.state == TaskState.PLAN
                    and child.state in _EPIC_ACTIVE_STATES
                ):
                    # Activate (HATS-692): work has started under a planned epic.
                    epic.transition_to(TaskState.EXECUTE)  # PLAN -> EXECUTE
                    reason = (
                        f"activated: child {child.id} ({child.state.value}) taken"
                    )
                    epic.log_work(f"Auto-activated plan -> execute ({reason})")
                    # No _setup_worktree — epics never get a worktree here.

            if reason is None:
                return []

            epic.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._save_task(epic)
            self._update_indexes()
            return [TaskTransition(epic, from_state, epic.state, reason)]

    def _setup_worktree(self, task: TaskCard) -> Path | None:
        """Create or adopt an isolated worktree when task enters execute state.

        HATS-061: each task gets its own worktree state slot — no singleton
        conflict between parallel tasks.

        HATS-479: if a concurrent ai-hats peer creates the same task's
        worktree between our pre-check and our ``create()``, the L1+L2
        defense raises :class:`WorktreeCreateError`. We re-fetch and adopt
        the peer's worktree — both transitions converge on one worktree.

        Returns the adopted linked-worktree path if invoked from inside one
        (HATS-060 short-circuit), the existing / created / adopted worktree
        path on the happy path, or None for non-git projects.
        """
        from .worktree import (
            WorktreeCreateError,
            WorktreeManager,
            assert_head_is_canonical_base,
        )

        # HATS-060: invoked from inside a linked worktree → adopt it.
        if WorktreeManager.is_inside_linked_worktree(self.project_dir):
            return self.project_dir

        # Per-task lookup (HATS-061) — fast-path, avoids the create-lock
        # roundtrip on the common case. The lock is acquired inside create()
        # for the actual decision.
        existing = WorktreeManager.load_for_task(self.project_dir, task.id)
        if existing is not None:
            return existing.worktree_path

        # HATS-518: only fires on a fresh create, not on the two adopt paths
        # above (no new branch capture happens in either). Raises
        # WorktreeBaseBranchError → caller translates to red exit.
        assert_head_is_canonical_base(self.project_dir)

        # No existing worktree for this task — create one.
        branch = f"task/{task.id.lower()}"
        mgr = WorktreeManager(self.project_dir, branch_name=branch)
        try:
            path = mgr.create()
        except WorktreeCreateError:
            # HATS-479: race-loser — another process won between our
            # pre-check and the L2 re-check under the create lock. Adopt
            # the peer's worktree instead of failing the transition.
            existing = WorktreeManager.load_for_task(self.project_dir, task.id)
            if existing is not None:
                logger.info(
                    "Adopted concurrently-created worktree for %s at %s",
                    task.id, existing.worktree_path,
                )
                return existing.worktree_path
            # Truly failed (state not findable) — propagate.
            raise
        if path != self.project_dir:  # git repo — worktree created
            mgr.save_state()
            return path
        return None

    def _teardown_worktree(
        self, task: TaskCard, *, merge: bool = True, force: bool = False
    ) -> None:
        """Merge or discard the worktree for a specific task (HATS-061).

        HATS-481 — fail-loud for merge failures. Previously this method
        swallowed ALL exceptions at WARNING and let ``transition`` continue
        to ``_save_task``, marking the task DONE even when merge failed →
        silent data loss class (same category as GitHub Merge Queue
        Apr-2026 incident). Now:

        * ``merge=True`` (``transition done``) re-raises any merge failure
          except :class:`OriginalBranchMissingError` (branch deleted —
          work is preserved on the worktree branch; user rebases manually).
          The transition aborts; task stays in ``review`` and the user
          retries after resolving the contention or conflict.
        * ``merge=False`` (``transition failed`` / ``transition cancelled``)
          keeps the swallowing behavior — the user is dropping the work
          administratively, so an orphaned worktree dir is a minor sin
          compared to refusing the admin close.

        HATS-596 — ``force`` is forwarded into :meth:`Worktree.merge` on the
        ``merge=True`` path so a corrective ``transition done --force`` can
        bypass the uncommitted-changes (``_check_clean``) gate, mirroring
        ``wt merge --force``. It does NOT relax the HEAD-mismatch guard —
        that stays a correctness gate against wrong-branch merges. The
        ``merge=False`` path already discards with ``force=True``.
        """
        from .worktree import (
            OriginalBranchMissingError,
            WorktreeManager,
            WorktreeStateLostError,
        )

        active = WorktreeManager.load_for_task(self.project_dir, task.id)
        if active is None:
            # HATS-541 fail-loud, defense-in-depth: a worktree state may be
            # gone while a ``task/<id>`` branch still exists. Post-HATS-587
            # a *failed* ``Worktree.merge()`` no longer produces this orphan
            # (F5: merge failure preserves worktree + state + branch for a
            # clean retry). The guard still covers the residual causes:
            # manual ``rm`` of the state JSON, a crash between
            # ``_remove_worktree`` and ``_clear_state`` on the SUCCESS path,
            # and pre-587 orphans created before that fix landed.
            #
            # A silent return here on the ``merge=True`` path would let
            # ``_save_task`` stamp DONE without any merge ever happening —
            # the silent-data-loss class HATS-481/541 exist to prevent.
            #
            # If a ``task/<id>`` branch still exists in the repo, refuse
            # the transition and point the user at manual recovery.
            # If the branch is also absent (true admin no-op, or
            # legitimately never had a worktree), keep silent return.
            #
            # merge=False (discard) path: stay silent — discard is
            # intentionally lossy by design, and refusing it would
            # block admin closes.
            if merge:
                branch_name = f"task/{task.id.lower()}"
                if WorktreeManager.branch_exists(self.project_dir, branch_name):
                    raise WorktreeStateLostError(task.id, branch_name)
            return

        try:
            if merge:
                active.merge(force=force)  # HATS-596: force reaches merge guards
            else:
                active.discard(force=True)  # failed → intentional discard
        except OriginalBranchMissingError as exc:
            # Branch deleted between create and teardown — keep current
            # behavior: warn but let the transition complete. The worktree
            # branch is preserved by WorktreeManager.merge; user rebases
            # manually. The work is NOT lost — it's just on a detached branch.
            logger.warning("Worktree merge skipped: %s", exc)
        except Exception:
            if merge:
                # HATS-481 fail-loud: re-raise so `transition` aborts before
                # `_save_task` marks the task DONE. Post-HATS-587 (F5) the
                # worktree dir, branch AND state JSON are all preserved by
                # WorktreeManager.merge on the exception path — the next
                # `transition done` is a clean retry once the operator
                # resolves the conflict (no manual `git merge --no-ff`).
                logger.error(
                    "Worktree merge failed for task %s, branch '%s' and "
                    "worktree preserved. Task NOT marked done — resolve and "
                    "retry.",
                    task.id, active.branch_name,
                )
                raise
            # merge=False (failed / cancelled administrative close): swallow.
            logger.warning(
                "Worktree discard failed, branch '%s' preserved",
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

    def _is_empty_scaffold(self, task: TaskCard) -> bool:
        plan_path = self.tasks_dir / task.id / "plan.md"
        if not plan_path.exists():
            return True
        expected = PLAN_SCAFFOLD.format(task_id=task.id, title=task.title)
        try:
            return plan_path.read_text() == expected
        except OSError:
            return False

    def _unfilled_sections(self, task: TaskCard) -> list[str]:
        """Names of REQUIRED plan sections that have no body content.

        A section is "filled" if there is at least one non-whitespace line
        between its `## <name>` heading and the next level-2 heading (or EOF).
        A required section whose heading is absent counts as unfilled. The
        plan→execute gate (see `transition`) blocks while this is non-empty.

        Reads the SAME `PLAN_SECTIONS` schema the scaffold renders from, so the
        template the agent fills and the gate that checks it cannot drift.
        """
        plan_path = self.tasks_dir / task.id / "plan.md"
        try:
            text = plan_path.read_text()
        except OSError:
            # No readable plan → every required section is unfilled.
            return [s.name for s in PLAN_SECTIONS if s.required]

        # Bucket body lines under their owning level-2 heading. `^##\s+`
        # matches a level-2 heading only: a level-3 `### x` fails (its third
        # char is `#`, not whitespace), and the H1 title fails too — so neither
        # opens a section.
        bodies: dict[str, list[str]] = {}
        current: str | None = None
        for line in text.splitlines():
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m:
                current = m.group(1)
                bodies.setdefault(current, [])
            elif current is not None:
                bodies[current].append(line)

        unfilled: list[str] = []
        for section in PLAN_SECTIONS:
            if not section.required:
                continue
            body = bodies.get(section.name)
            if body is None or not "".join(body).strip():
                unfilled.append(section.name)
        return unfilled

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
        from .safe_delete import discard as _safe_discard

        headers = self._iter_headers()
        self._update_state_md(headers)
        if self._legacy_backlog_md_path.exists():
            _safe_discard(
                self._legacy_backlog_md_path,
                reason="legacy-backlog",
                project_dir=self.project_dir,
            )

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

        # HATS-470: STATE.md regen is high-frequency (every state-changing
        # task command). bytes-identical replace is a no-op, so user-edits
        # between commands are snapshotted but the steady-state pure regen
        # doesn't churn /tmp. If this proves noisy in practice, convert
        # to a whitelist-marker site (regen is deterministic from tasks/).
        from .safe_delete import replace as _safe_replace
        self.state_md_path.parent.mkdir(parents=True, exist_ok=True)
        _safe_replace(
            self.state_md_path,
            ("\n".join(lines) + "\n").encode("utf-8"),
            reason="state-md-regen",
            project_dir=self.project_dir,
        )
