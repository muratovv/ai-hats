"""Integrator-side worktree effects — the wt binding for the tracker FSM.

ADR-0014 P0 #3 / HATS-866: the tracker FSM (:class:`ai_hats_tracker.state.TaskManager`)
emits worktree side-effects through the :class:`ai_hats_tracker.state.WorktreeEffects`
protocol; THIS module is the only binding of those effects to :mod:`ai_hats_wt`.
``cli/_helpers._task_manager`` injects it; a ``TaskManager`` without a handler
is a pure FSM (no worktree is created or torn down).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def collect_carry_for_project(
    project_dir: Path, role: str = ""
) -> dict[str, list[dict[str, object]]]:
    """Collect the effective role's worktree carry (fail-open, HATS-865).

    Compose lives in ``composition_seam.compose_for_carry``; the chokepoint
    receives the ready result + hooks manager.
    """
    from .composition_seam import compose_for_carry
    from .wt_carry import collect_carry_for_role

    composed = compose_for_carry(project_dir, role)
    if composed is None:
        return {}
    result, hooks = composed
    return collect_carry_for_role(project_dir, result, hooks)


class WtWorktreeEffects:
    """wt-backed :class:`ai_hats_tracker.state.WorktreeEffects` implementation.

    Bodies moved verbatim from ``TaskManager._setup_worktree`` /
    ``_teardown_worktree`` (HATS-866) — semantics unchanged; wt exceptions
    propagate to the caller (the CLI translates them to red exits).
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def assert_canonical_base(self) -> None:
        """HATS-518 guard for the forced-execute path (no worktree is created)."""
        from ai_hats_wt import assert_head_is_canonical_base

        from .wt_config import resolve_worktree_branches

        _base, merge_target = resolve_worktree_branches(self.project_dir)  # HATS-942
        assert_head_is_canonical_base(self.project_dir, merge_target)

    def setup(self, task_id: str, role: str = "", caller_cwd: Path | None = None) -> Path | None:
        """Create or adopt the task's isolated worktree on ``→ execute``.

        Returns the worktree path — adopted (caller already inside one,
        HATS-060/840; racing peer's create, HATS-479), the task's existing one
        (HATS-061), or freshly created — or None for non-git projects.
        """
        from ai_hats_wt import (
            WorktreeCreateError,
            WorktreeManager,
            assert_head_is_canonical_base,
        )

        from .paths import worktrees_dir
        from .wt_lifecycle import HOOK_LIFECYCLE

        # Probe order: adopt the worktree the caller is in (HATS-060/840) → reuse
        # the task's existing one (HATS-061) → guard canonical base (HATS-518) →
        # create with the role's carry (HATS-823); racing peer wins by adoption (479).
        wt_state_dir = worktrees_dir(self.project_dir)

        adopt_probe = caller_cwd if caller_cwd is not None else self.project_dir
        if WorktreeManager.is_inside_linked_worktree(adopt_probe):
            return WorktreeManager.worktree_toplevel(adopt_probe) or adopt_probe

        existing = WorktreeManager.load_for_task(self.project_dir, task_id, state_dir=wt_state_dir)
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
            lifecycle=HOOK_LIFECYCLE,
            state_dir=wt_state_dir,
        )
        wt_hooks = collect_carry_for_project(self.project_dir, role)
        try:
            path = mgr.create(wt_hooks=wt_hooks)
        except WorktreeCreateError:
            existing = WorktreeManager.load_for_task(
                self.project_dir, task_id, state_dir=wt_state_dir
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

    def teardown(self, task_id: str, *, merge: bool = True, force: bool = False) -> str | None:
        """Merge (``merge=True``) or discard the task's worktree.

        Returns "merged" / "discarded" for the card's work_log (HATS-866/AC5),
        or None when no worktree action actually happened. Merge failures
        re-raise so the transition aborts fail-loud (HATS-481); ``force``
        bypasses only the clean-tree merge gate (HATS-596); discard failures
        on an admin close are swallowed.
        """
        from ai_hats_wt import (
            OriginalBranchMissingError,
            WorktreeManager,
            WorktreeStateLostError,
        )

        from .paths import worktrees_dir
        from .wt_lifecycle import HOOK_LIFECYCLE

        # Manager rebuilt with the hook bundle + injected state-dir (ADR-0013 D3/D4).
        # State lost: branch already merged → finalize without re-merge (HATS-697),
        # genuinely un-merged → fail-loud (WorktreeStateLostError).
        active = WorktreeManager.load_for_task(
            self.project_dir,
            task_id,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=worktrees_dir(self.project_dir),
        )
        if active is None:
            if merge:
                branch_name = f"task/{task_id.lower()}"
                if WorktreeManager.branch_exists(self.project_dir, branch_name):
                    base = WorktreeManager.branch_merged_into_canonical_base(
                        self.project_dir, branch_name
                    )
                    if base is None:
                        raise WorktreeStateLostError(task_id, branch_name)
                    WorktreeManager.delete_merged_branch(self.project_dir, branch_name)
                    logger.info(
                        "Task %s branch '%s' already merged into '%s' — "
                        "finalizing without re-merge (HATS-697)",
                        task_id,
                        branch_name,
                        base,
                    )
                    return "merged"
            return None

        try:
            if merge:
                active.merge(force=force)  # HATS-596: force reaches merge guards
                return "merged"
            active.discard(force=True)  # failed → intentional discard
            return "discarded"
        except OriginalBranchMissingError as exc:
            # Original branch deleted — work survives on the worktree branch.
            logger.warning("Worktree merge skipped: %s", exc)
        except Exception:
            if merge:
                logger.error(
                    "Worktree merge failed for task %s, branch '%s' and "
                    "worktree preserved. Task NOT marked done — resolve and "
                    "retry.",
                    task_id,
                    active.branch_name,
                )
                raise
            # merge=False (failed / cancelled administrative close): swallow.
            logger.warning(
                "Worktree discard failed, branch '%s' preserved",
                active.branch_name,
                exc_info=True,
            )
