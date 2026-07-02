"""ai-hats worktree lifecycle bundle — the hook-running extension-point impl.

ADR-0013 P1 / HATS-849. The worktree *core* (:mod:`ai_hats_wt`) is
hook-agnostic: it fires ``on_created`` / ``before_teardown`` extension-points at
each lifecycle site and owns the per-route teardown control-flow, but knows
nothing about hooks. THIS module is the ai-hats accretion that plugs in: it
decides *what* runs at those points (the component-declared ``wt_in`` / ``wt_out``
scripts via :func:`ai_hats.worktree_hooks.run_worktree_hook`), the fail-vs-warn
policy, the ``skip_hooks`` escape, and the legacy warn-not-drop. ai-hats injects
:data:`HOOK_LIFECYCLE` at every ``WorktreeManager`` construction / load; a bare
core keeps the no-op default and runs no hooks.

Policy (ADR-0012 D3/D7, relocated here from the engine):

- ``wt_in`` (``on_created``) — **warn-continue**: a create-time failure is
  friction, not data loss; it is logged and never raises.
- ``wt_out`` (``before_teardown``) — **fail-closed**: a hook failure raises the
  core-owned :class:`ai_hats_wt.WorktreeTeardownAborted` (with the
  :class:`WorktreeHookError` riding as ``__cause__``), aborting teardown and
  preserving the worktree. The core then propagates (merge/discard) or
  suppresses (cleanup) per route.
"""

from __future__ import annotations

import logging
from typing import NoReturn

from .models import WT_TEARDOWN_EVENTS
from .paths import managed_wt_hook_filename, wt_hooks_dir
from .worktree_hooks import run_worktree_hook
from ai_hats_wt import LifecycleContext, WorktreeTeardownAborted
from ai_hats_wt.locks import _state_key

logger = logging.getLogger(__name__)


class WorktreeHookError(Exception):
    """A ``wt_out`` lifecycle hook failed and teardown is fail-closed (HATS-823).

    Relocated from the worktree engine by ADR-0013 D8: this is *hook
    vocabulary*, so it belongs with the lifted hook layer, not the hook-agnostic
    core. It never escapes this module as the raised type — it rides as the
    ``__cause__`` of the core :class:`ai_hats_wt.WorktreeTeardownAborted`,
    which the CLI surfaces via ``str(e.__cause__)``.
    """


def _wt_hook_log_dir(state_dir, branch_name: str):
    # ADR-0013 D4 / HATS-851: resolve hook-logs off the manager's INJECTED
    # state-dir base (ctx.state_dir), not a recomputed worktrees_dir(project_dir),
    # so state + hook-logs stay co-located even under a custom-base driver.
    return state_dir / f"{_state_key(branch_name)}.logs"


def _materialized_hook(project_dir, row: dict):
    """On-disk path of a hook script the assembler materialized."""
    return wt_hooks_dir(project_dir) / managed_wt_hook_filename(
        row["skill"], row["script"]
    )


class HookRunningLifecycle:
    """Runs component-declared ``wt_in`` / ``wt_out`` hooks at the core lifecycle
    sites. Stateless — every input comes from the :class:`LifecycleContext`, so a
    single module-level instance (:data:`HOOK_LIFECYCLE`) serves all managers."""

    def on_created(self, ctx: LifecycleContext) -> None:
        """Run ``wt_in`` hooks after ``git worktree add`` (warn-and-continue).

        A create-time hook failure is friction, not data loss (ADR-0012 D3/D7):
        logged and skipped, never aborting worktree creation.
        """
        rows = ctx.carry.get("wt_in") or []
        if not rows or ctx.worktree_path is None:
            return
        log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
        for row in rows:
            script = _materialized_hook(ctx.project_dir, row)
            outcome = run_worktree_hook(
                script,
                event="wt_in",
                worktree_path=ctx.worktree_path,
                project_dir=ctx.project_dir,
                branch_name=ctx.branch_name,
                log_path=log_dir / f"wt_in-{script.name}.log",
            )
            if not outcome.ok:
                logger.warning(
                    "wt_in hook from skill '%s' failed — continuing "
                    "(create-time friction, not data loss): %s",
                    row.get("skill", "?"),
                    outcome.reason,
                )

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        """Run ``wt_out`` hooks bound to ``event`` before the core removes the dir.

        Fail-closed: a hook failure raises
        :class:`ai_hats_wt.WorktreeTeardownAborted` (with a
        :class:`WorktreeHookError` ``__cause__``); the core aborts teardown and
        preserves the worktree. ``ctx.skip_hooks`` is the conscious escape; a
        legacy (carry-less) state warns but does not drop.
        """
        if ctx.legacy:
            # Pre-upgrade worktree: can't know what it holds → warn, don't drop.
            logger.warning(
                "Worktree '%s' predates wt-hooks (no carry recorded at create) "
                "— gitignored data cannot be auto-harvested on %s; back it up "
                "manually if needed.",
                ctx.branch_name,
                event,
            )
        rows = [
            r
            for r in (ctx.carry.get("wt_out") or [])
            if event in (r.get("on") or WT_TEARDOWN_EVENTS)
        ]
        if ctx.worktree_path is None or not rows:
            return
        if ctx.skip_hooks:
            logger.warning(
                "wt_out hooks SKIPPED for '%s' on %s via --skip-hooks — "
                "unharvested gitignored data will be destroyed (%d hook(s))",
                ctx.branch_name,
                event,
                len(rows),
            )
            return
        log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
        for row in rows:
            script = _materialized_hook(ctx.project_dir, row)
            outcome = run_worktree_hook(
                script,
                event=event,
                worktree_path=ctx.worktree_path,
                project_dir=ctx.project_dir,
                branch_name=ctx.branch_name,
                log_path=log_dir / f"{event}-{script.name}.log",
            )
            if not outcome.ok:
                _raise_teardown_aborted(event, ctx.branch_name, row, outcome.reason)


def _raise_teardown_aborted(
    event: str, branch_name: str, row: dict, reason: str
) -> NoReturn:
    """Raise the core abort wrapping a :class:`WorktreeHookError` cause (D8).

    The ``__cause__`` carries the full recovery recipe. On the propagated
    merge/discard routes the CLI surfaces ``str(e.__cause__)`` and the FSM
    surfaces ``str(exc)`` — so the abort message there is kept identical to the
    cause (rich on both). The ``cleanup`` route SUPPRESSES the abort and logs
    only ``str(exc)`` (the cause is never surfaced), so its abort message must
    itself name the sub-agent recovery (D8 provenance).
    """
    skill = row.get("skill", "?")
    detail = (
        f"wt_out hook from skill '{skill}' failed on {event} ({reason}). "
        f"Teardown aborted — worktree '{branch_name}' preserved. Fix the hook "
        f"and retry, or force with --skip-hooks (accepts the data loss)."
    )
    cause = WorktreeHookError(detail)
    if event == "cleanup":
        message = (
            f"wt_out hook failed on cleanup — recover with "
            f"`ai-hats wt discard {branch_name} --skip-hooks`: {reason}"
        )
    else:
        message = detail
    raise WorktreeTeardownAborted(message) from cause


#: The bundle ai-hats injects at every WorktreeManager construction / load.
HOOK_LIFECYCLE: HookRunningLifecycle = HookRunningLifecycle()
