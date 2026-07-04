"""Integrator-side worktree effects — the wt binding for the tracker FSM.

ADR-0014 P0 #3 / HATS-866: the tracker FSM (:class:`ai_hats.state.TaskManager`)
emits worktree side-effects through the :class:`ai_hats.state.WorktreeEffects`
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
    """wt-backed :class:`ai_hats.state.WorktreeEffects` implementation.

    Bodies moved verbatim from ``TaskManager._setup_worktree`` /
    ``_teardown_worktree`` (HATS-866) — semantics unchanged; wt exceptions
    propagate to the caller (the CLI translates them to red exits).
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def assert_canonical_base(self) -> None:
        """HATS-518 guard for the forced-execute path (no worktree is created)."""
        from ai_hats_wt import assert_head_is_canonical_base

        assert_head_is_canonical_base(self.project_dir)

    def setup(self, task_id: str, role: str = "", caller_cwd: Path | None = None) -> Path | None:
        """Create or adopt an isolated worktree when a task enters execute state.

        HATS-061: each task gets its own worktree state slot — no singleton
        conflict between parallel tasks.

        HATS-479: if a concurrent ai-hats peer creates the same task's
        worktree between our pre-check and our ``create()``, the L1+L2
        defense raises :class:`WorktreeCreateError`. We re-fetch and adopt
        the peer's worktree — both transitions converge on one worktree.

        HATS-840: the adopt short-circuit keys on ``caller_cwd`` (the operator's
        raw cwd from the CLI), not the main-hopped ``self.project_dir``; ``None``
        falls back to ``self.project_dir``.

        Returns the adopted linked-worktree's own toplevel if invoked from inside
        one (HATS-060 short-circuit), the existing / created / adopted worktree
        path on the happy path, or None for non-git projects.
        """
        from ai_hats_wt import (
            WorktreeCreateError,
            WorktreeManager,
            assert_head_is_canonical_base,
        )

        from .paths import worktrees_dir
        from .wt_lifecycle import HOOK_LIFECYCLE

        # ADR-0013 D4: ai-hats injects its state-dir convention at every
        # construct/load so the core never falls back project-local.
        wt_state_dir = worktrees_dir(self.project_dir)

        # HATS-060 / HATS-840: adopt the worktree the operator is in. Probe the
        # threaded `caller_cwd`, not the main-hopped `self.project_dir`.
        adopt_probe = caller_cwd if caller_cwd is not None else self.project_dir
        if WorktreeManager.is_inside_linked_worktree(adopt_probe):
            return WorktreeManager.worktree_toplevel(adopt_probe) or adopt_probe

        # Per-task lookup (HATS-061) — fast-path, avoids the create-lock
        # roundtrip on the common case. The lock is acquired inside create()
        # for the actual decision.
        existing = WorktreeManager.load_for_task(self.project_dir, task_id, state_dir=wt_state_dir)
        if existing is not None:
            return existing.worktree_path

        # HATS-518: only fires on a fresh create, not on the two adopt paths
        # above (no new branch capture happens in either). Raises
        # WorktreeBaseBranchError → caller translates to red exit.
        assert_head_is_canonical_base(self.project_dir)

        # No existing worktree for this task — create one. HATS-823: thread the
        # worktree's role carry (wt_in/wt_out hooks) in at create; persisted to
        # state so teardown runs the create-time set (D3).
        branch = f"task/{task_id.lower()}"
        mgr = WorktreeManager(
            self.project_dir,
            branch_name=branch,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=wt_state_dir,
        )
        wt_hooks = collect_carry_for_project(self.project_dir, role)
        try:
            path = mgr.create(wt_hooks=wt_hooks)
        except WorktreeCreateError:
            # HATS-479: race-loser — another process won between our
            # pre-check and the L2 re-check under the create lock. Adopt
            # the peer's worktree instead of failing the transition.
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
            # Truly failed (state not findable) — propagate.
            raise
        if path != self.project_dir:  # git repo — worktree created
            mgr.save_state()
            return path
        return None

    def teardown(self, task_id: str, *, merge: bool = True, force: bool = False) -> None:
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
        from ai_hats_wt import (
            OriginalBranchMissingError,
            WorktreeManager,
            WorktreeStateLostError,
        )

        from .paths import worktrees_dir
        from .wt_lifecycle import HOOK_LIFECYCLE

        # ADR-0013 D3: reconstruct the teardown manager with ai-hats's
        # hook-running bundle so before_teardown fires wt_out hooks fail-closed.
        # D4: pass the state-dir convention so teardown resolves the same dir
        # create wrote to (a missing base would orphan the state).
        active = WorktreeManager.load_for_task(
            self.project_dir,
            task_id,
            lifecycle=HOOK_LIFECYCLE,
            state_dir=worktrees_dir(self.project_dir),
        )
        if active is None:
            # State JSON gone but the branch may survive (manual rm,
            # success-path crash, pre-587 orphan). On merge=True a silent
            # return would stamp DONE with no merge — the HATS-481/541
            # silent-data-loss class. So when the branch exists:
            # already-merged → finalize without re-merge (HATS-697, the
            # state-lost twin of the HATS-596 short-circuit) + drop the stale
            # ref; genuinely un-merged → fail-loud (force is NOT a data-loss
            # hatch). Branch absent, or merge=False discard → silent.
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
