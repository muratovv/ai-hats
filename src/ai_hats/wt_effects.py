"""Integrator-side worktree effects — the wt binding for the backlog FSM.

ADR-0014 P0 #3 / HATS-866: the FSM emits worktree side-effects through a
``WorktreeEffects`` protocol; THIS module is the only binding of those effects
to :mod:`ai_hats_wt`. ``rack_wiring.build_rack_kernel`` injects it (HATS-1262:
the `ai_hats_tracker` package that once owned both names is deleted); a kernel
without a handler is a pure FSM (no worktree).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_hats_core.deadline import Deadline

logger = logging.getLogger(__name__)


def collect_carry_for_project(
    project_dir: Path, role: str = ""
) -> dict[str, list[dict[str, object]]]:
    """Collect the effective role's worktree carry (fail-open, HATS-865).

    Compose lives in ``composition_seam.compose_for_carry``; the chokepoint
    receives the ready result.
    """
    from .composition_seam import compose_for_carry
    from .wt_carry import collect_carry_for_role

    return collect_carry_for_role(compose_for_carry(project_dir, role))


class WtWorktreeEffects:
    """wt-backed ``WorktreeEffects`` implementation.

    Bodies moved verbatim from ``TaskManager._setup_worktree`` /
    ``_teardown_worktree`` (HATS-866) — semantics unchanged; wt exceptions
    propagate to the caller (the CLI translates them to red exits).
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        git_timeout: float | None = None,
        lifecycle: object | None = None,
    ) -> None:
        self.project_dir = project_dir
        # HATS-1015 liveness budget: the per-git wall-clock ceiling threaded into
        # every worktree shell-out (default None = unbounded — behaviour unchanged).
        self._git_timeout = git_timeout
        if lifecycle is None:  # resolved once here, not re-imported per method
            from .wt_lifecycle import HOOK_LIFECYCLE

            lifecycle = HOOK_LIFECYCLE
        self._lifecycle = lifecycle

    def assert_canonical_base(self) -> None:
        """HATS-518 guard for the forced-execute path (no worktree is created)."""
        from ai_hats_wt import assert_head_is_canonical_base

        from .wt_config import resolve_worktree_branches

        _base, merge_target = resolve_worktree_branches(self.project_dir)  # HATS-942
        assert_head_is_canonical_base(self.project_dir, merge_target)

    def setup(
        self,
        task_id: str,
        role: str = "",
        caller_cwd: Path | None = None,
        *,
        outer_deadline: Deadline | None = None,
    ) -> Path | None:
        """Create or adopt the task's isolated worktree on ``→ execute``.

        Returns the worktree path — adopted (caller already inside one,
        HATS-060/840; racing peer's create, HATS-479), the task's existing one
        (HATS-061), or freshly created — or None for non-git projects.
        ``outer_deadline`` is the caller's ceiling (HATS-1603).
        """
        from ai_hats_wt import (
            WorktreeCreateError,
            WorktreeManager,
            assert_head_is_canonical_base,
        )

        from .paths import worktree_checkouts_dir, worktrees_dir

        # Probe order: adopt the worktree the caller is in (HATS-060/840) → reuse
        # the task's existing one (HATS-061) → guard canonical base (HATS-518) →
        # create with the role's carry (HATS-823); racing peer wins by adoption (479).
        wt_state_dir = worktrees_dir(self.project_dir)

        adopt_probe = caller_cwd if caller_cwd is not None else self.project_dir
        if WorktreeManager.is_inside_linked_worktree(adopt_probe):
            return WorktreeManager.worktree_toplevel(adopt_probe) or adopt_probe

        existing = WorktreeManager.load_for_task(
            self.project_dir, task_id, state_dir=wt_state_dir, git_timeout=self._git_timeout
        )
        if existing is not None:
            return existing.worktree_path

        from .wt_config import resolve_worktree_branches

        base_branch, merge_target = resolve_worktree_branches(self.project_dir)  # HATS-942
        assert_head_is_canonical_base(self.project_dir, merge_target)

        branch = f"task/{task_id.lower()}"
        mgr = WorktreeManager(
            self.project_dir,
            branch_name=branch,
            base_branch=base_branch,
            merge_target=merge_target,
            lifecycle=self._lifecycle,
            state_dir=wt_state_dir,
            worktree_checkouts_dir=worktree_checkouts_dir(self.project_dir),  # HATS-1632
            git_timeout=self._git_timeout,
        )
        wt_hooks = collect_carry_for_project(self.project_dir, role)
        try:
            path = mgr.create(wt_hooks=wt_hooks, outer_deadline=outer_deadline)
        except WorktreeCreateError:
            existing = WorktreeManager.load_for_task(
                self.project_dir, task_id, state_dir=wt_state_dir, git_timeout=self._git_timeout
            )
            if existing is not None:
                logger.info(
                    "Adopted concurrently-created worktree for %s at %s",
                    task_id,
                    existing.worktree_path,
                )
                return existing.worktree_path
            raise
        if path != self.project_dir:  # git repo — worktree created
            mgr.save_state()
            return path
        return None

    def teardown(
        self,
        task_id: str,
        *,
        merge: bool = True,
        force: bool = False,
        outer_deadline: Deadline | None = None,
    ) -> str | None:
        """Merge (``merge=True``) or discard the task's worktree.

        Returns "merged" / "discarded" for the card's work_log (HATS-866/AC5),
        or None when no worktree action actually happened. Merge failures
        re-raise so the transition aborts fail-loud (HATS-481); ``force``
        bypasses only the clean-tree merge gate (HATS-596); discard failures
        on an admin close are swallowed. ``outer_deadline`` is the caller's
        ceiling (the rack task lock on the FSM road) — HATS-1603.
        """
        from ai_hats_wt import (
            OriginalBranchMissingError,
            WorktreeManager,
            WorktreeStateLostError,
        )

        from .paths import worktrees_dir

        # Manager rebuilt with the hook bundle + injected state-dir (ADR-0013 D3/D4).
        # State lost: branch already merged → finalize without re-merge (HATS-697),
        # genuinely un-merged → fail-loud (WorktreeStateLostError).
        active = WorktreeManager.load_for_task(
            self.project_dir,
            task_id,
            lifecycle=self._lifecycle,
            state_dir=worktrees_dir(self.project_dir),
            git_timeout=self._git_timeout,
        )
        if active is None:
            if merge:
                branch_name = f"task/{task_id.lower()}"
                if WorktreeManager.branch_exists(
                    self.project_dir, branch_name, timeout=self._git_timeout
                ):
                    base = WorktreeManager.branch_merged_into_canonical_base(
                        self.project_dir, branch_name, timeout=self._git_timeout
                    )
                    if base is None:
                        raise WorktreeStateLostError(task_id, branch_name)
                    WorktreeManager.delete_merged_branch(
                        self.project_dir, branch_name, timeout=self._git_timeout
                    )
                    logger.info(
                        "Task %s branch '%s' already merged into '%s' — "
                        "finalizing without re-merge",
                        task_id,
                        branch_name,
                        base,
                    )
                    return "merged"
            return None

        try:
            if merge:
                # HATS-596: force reaches merge guards. HATS-1603: so does the
                # caller's ceiling, or wt:pre-merge outlives the rack lock.
                active.merge(force=force, outer_deadline=outer_deadline)
                return "merged"
            # failed → intentional discard; same ceiling as merge (HATS-1603)
            active.discard(force=True, outer_deadline=outer_deadline)
            return "discarded"
        except OriginalBranchMissingError as exc:
            # Original branch deleted — work survives on the worktree branch.
            logger.warning("Worktree merge skipped: %s", exc)
        except Exception as exc:
            if merge:
                logger.error(
                    "Worktree merge failed for task %s, branch '%s' and "
                    "worktree preserved. Task NOT marked done — resolve and "
                    "retry.",
                    task_id,
                    active.branch_name,
                )
                raise
            # merge=False (failed / cancelled administrative close): swallowed,
            # so the transition succeeds — a stack trace here reads as a crash
            # for work that completed (HATS-1332). Name the cause on one line.
            logger.warning(
                "Worktree discard failed, branch '%s' preserved: %s: %s",
                active.branch_name,
                type(exc).__name__,
                exc,
            )

    def discard_if_empty(self, task_id: str) -> bool:
        """Reclaim ``task_id``'s worktree iff it has no unmerged work (HATS-979).

        Called at epicification: a task that gained a child is a tracker now, so
        its execute-time worktree is dead weight. Kept when it carries a dirty
        tree, own unmerged commits, or pending hunk review. True if reclaimed.
        """
        from ai_hats_wt import WorktreeManager

        from .paths import worktrees_dir

        active = WorktreeManager.load_for_task(
            self.project_dir,
            task_id,
            lifecycle=self._lifecycle,
            state_dir=worktrees_dir(self.project_dir),
            git_timeout=self._git_timeout,
        )
        if active is None:
            return False
        try:
            # HATS-818: `git status` (reclaim's own check) can't see the
            # gitignored `.hunk/notes.json`, so inject the pending-review probe as
            # an extra hold — a worktree under un-addressed review is kept.
            return bool(active.reclaim_if_clean(has_extra_hold=_has_pending_hunk_review))
        except Exception:
            # Best-effort: a worktree-reclaim hiccup must never fail the
            # child-creation / re-parent that triggered it. Keep + log.
            logger.warning(
                "Worktree reclaim failed for epic %s, worktree preserved",
                task_id,
                exc_info=True,
            )
            return False


def _has_pending_hunk_review(worktree_path: Path | None) -> bool:
    """True if un-drained hunk review notes live in the worktree (HATS-818)."""
    if worktree_path is None:
        return False
    notes = worktree_path / ".hunk" / "notes.json"
    try:
        return notes.is_file() and notes.read_text().strip() not in ("", "[]", "{}", "null")
    except OSError:
        return False
