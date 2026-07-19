"""Integrator-side rack adapters: ownership + worktree extensions and the
kernel factory (HATS-1022, epic HATS-1014 K3).

The rack never imports the integrator (import-hygiene pin); THIS module is
the one-directional binding of the rack dispatcher to the production
ownership registry and the wt engine. ``build_rack_kernel`` mirrors
``cli/_helpers._task_manager`` and preserves the tracker's side-effect
order: single-slot guard → plan-gate → claim → worktree; teardown → release.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from ai_hats_rack import Kernel
from ai_hats_rack.definition import resolve_definition
from ai_hats_rack.registry import LinksRegistry
from ai_hats_rack.dispatch import (
    AbortOperation,
    Delta,
    DispatchContext,
    JournalSink,
    Phase,
    Subscription,
)
from ai_hats_rack.events import EdgeEvent, EpicifyEvent, PreDestroyEvent
from ai_hats_rack.extensions import (
    DerivedViewsExtension,
    EpicAutomationExtension,
    FrozenIntegrityExtension,
    PlanGateExtension,
    PlanScaffoldExtension,
    Section,
)
from ai_hats_rack.fsm import Topology
from ai_hats_core import scrubbed_git_env
from ai_hats_tracker import ownership
from ai_hats_tracker.constants import ENV_ROOT_PID, ENV_SESSION_ID

from .paths import worktrees_dir
from .wt_effects import WtWorktreeEffects

TERMINAL_STATES = ("done", "failed", "cancelled")


def _all_edge_keys(topology: Topology) -> list[str]:
    """Every ``edge:<from>--<to>`` pair: forced transitions fire real
    (possibly non-topology) keys, so safety subscriptions enumerate the
    product, not just legal edges."""
    states = topology.states
    return [
        f"edge:{src}--{dst}"
        for src in states
        for dst in states
        if src != dst or src == "execute"  # + reclaim self-loop (HATS-955)
    ]


def _keys_into(topology: Topology, *targets: str) -> list[str]:
    return [k for k in _all_edge_keys(topology) if k.split("--")[-1] in targets]


def _keys_leaving_execute_or_terminal(topology: Topology) -> list[str]:
    out = []
    for key in _all_edge_keys(topology):
        src, dst = key.removeprefix("edge:").split("--")
        if (src == "execute" and dst != "execute") or dst in TERMINAL_STATES:
            out.append(key)
    return out


def _session_id() -> str:
    return os.environ.get(ENV_SESSION_ID, "")


def _root_pid() -> int:
    try:
        return int(os.environ.get(ENV_ROOT_PID, "") or 0)
    except ValueError:
        return 0


class OwnershipSingleSlot:
    """Single-slot guard on EVERY transition (HATS-955): refuse while the
    session still holds a different task. Runs before the plan-gate; read-only
    (an abort here or later leaves zero ownership side effects)."""

    name = "ownership-single-slot"

    def __init__(self, registry_path: Path, *, topology: Topology, priority: int = 5) -> None:
        self.registry_path = registry_path
        self._topology = topology
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(k, Phase.IN_LOCK, self._priority) for k in _all_edge_keys(self._topology)
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        session_id = _session_id()
        if not session_id or ctx.is_epic:  # epics are trackers (HATS-794)
            return None
        dangling = [
            t for t in ownership.held_by(self.registry_path, session_id) if t != ctx.task.id
        ]
        if dangling:
            raise AbortOperation(
                f"session '{session_id}' still holds {dangling} — finish it or leave "
                "execute on it first (single-slot ownership, HATS-955; force does not bypass)"
            )
        return None


class OwnershipClaim:
    """Claim on entering execute (incl. the reclaim self-loop), AFTER the
    plan-gate and BEFORE the worktree — a refusal aborts with zero side
    effects (HATS-955). A live other owner is a typed, actionable abort."""

    name = "ownership"

    def __init__(self, registry_path: Path, *, topology: Topology, priority: int = 20) -> None:
        self.registry_path = registry_path
        self._topology = topology
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(k, Phase.IN_LOCK, self._priority)
            for k in _keys_into(self._topology, "execute")
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        session_id = _session_id()
        if not session_id or ctx.is_epic:
            return None
        try:
            ownership.take(self.registry_path, ctx.task.id, session_id, _root_pid())
        except ownership.OwnershipRefused as exc:
            holder = f" (holder: '{exc.holder}')" if exc.holder else ""
            raise AbortOperation(
                f"ownership of {ctx.task.id} refused: {exc.reason}{holder} — wait for the "
                "owner to finish or reclaim once its process is dead (execute → execute); "
                "force does not bypass ownership"
            ) from exc
        return None


class OwnershipRelease:
    """Unconditional idempotent release on leaving execute / any terminal
    (HATS-977 — epics included) and on epicification (post-lock reaction).
    Runs AFTER the worktree teardown, so a failed merge keeps the hold."""

    name = "ownership-release"

    def __init__(
        self,
        registry_path: Path,
        *,
        topology: Topology,
        priority: int = 40,
        epicify_priority: int = 10,
    ) -> None:
        self.registry_path = registry_path
        self._topology = topology
        self._priority = priority
        self._epicify_priority = epicify_priority

    def subscriptions(self) -> Sequence[Subscription]:
        subs = [
            Subscription(k, Phase.IN_LOCK, self._priority)
            for k in _keys_leaving_execute_or_terminal(self._topology)
        ]
        subs.append(Subscription("epicify", Phase.POST_LOCK, self._epicify_priority))
        return subs

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if isinstance(ctx.event, EpicifyEvent):
            # HATS-977: a task that gained a child is a tracker now — drop its hold.
            ownership.finish(self.registry_path, ctx.event.epic_id)
            return None
        if _session_id():
            ownership.finish(self.registry_path, ctx.task.id)
        return None


class WorktreeExtension:
    """Worktree lifecycle adapter over the wt engine: setup on execute
    (except epics/reopen/force — HATS-794/328/697); teardown-merge on done,
    discard on failed/cancelled; git is the truth (HATS-596/697/PROX-287);
    force never bypasses the canonical-base guard (HATS-518); aborts a
    teardown from inside the tree (HATS-788); pre-destroy event before
    destruction (PROP-047); cancelled preserves uncommitted work (PROP-084);
    epicify reclaims an empty tree (HATS-979); repo-aware done-guard via the
    card's ``repo`` extra (PROP-056/057)."""  # comment-length: allow

    name = "worktree"

    def __init__(
        self,
        project_dir: Path,
        *,
        effects: WtWorktreeEffects | None = None,
        topology: Topology,
        setup_priority: int = 30,
        teardown_priority: int = 30,
        epicify_priority: int = 20,
    ) -> None:
        self.project_dir = project_dir
        self._effects = effects if effects is not None else WtWorktreeEffects(project_dir)
        self._topology = topology
        self._setup_priority = setup_priority
        self._teardown_priority = teardown_priority
        self._epicify_priority = epicify_priority
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        """Late-bound kernel handle for publishing pre-destroy events."""
        self._kernel = kernel

    def subscriptions(self) -> Sequence[Subscription]:
        subs = [
            Subscription(k, Phase.IN_LOCK, self._setup_priority)
            for k in _keys_into(self._topology, "execute")
        ]
        subs.extend(
            Subscription(k, Phase.IN_LOCK, self._teardown_priority)
            for k in _keys_into(self._topology, *TERMINAL_STATES)
        )
        subs.append(Subscription("epicify", Phase.POST_LOCK, self._epicify_priority))
        return subs

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if isinstance(ctx.event, EpicifyEvent):
            # HATS-979: reclaim the now-epic parent's worktree iff empty/merged.
            self._effects.discard_if_empty(ctx.event.epic_id)
            return None
        if not isinstance(ctx.event, EdgeEvent):
            return None
        if ctx.event.to_state == "execute":
            return self._on_execute(ctx)
        if ctx.event.to_state in TERMINAL_STATES:
            return self._on_teardown(ctx, merge=ctx.event.to_state == "done")
        return None

    # ----- execute entry ----------------------------------------------------

    def _on_execute(self, ctx: DispatchContext) -> Delta | None:
        if ctx.is_epic:
            return None  # epics never get a worktree (HATS-794)
        if ctx.event.from_state == "done":
            return None  # reopen: the operator owns the worktree decision (HATS-328)
        if ctx.force:
            # HATS-518: force relaxes the FSM arrow, NOT the canonical-base
            # contract — the guard must run explicitly on the force path.
            self._effects.assert_canonical_base()
            # HATS-697: a forced execute is a manual state correction — no
            # fresh worktree (one spun off HEAD orphaned retro work, PROX-287).
            return Delta(work_log=("Forced → execute: no worktree created (manual override)",))
        wt_path = self._effects.setup(ctx.task.id, ctx.task.role, caller_cwd=ctx.caller_cwd)
        if wt_path is not None:
            return Delta(work_log=(f"Worktree: {wt_path}",))  # HATS-866/AC5
        return None

    # ----- teardown ---------------------------------------------------------

    def _on_teardown(self, ctx: DispatchContext, *, merge: bool) -> Delta | None:
        task_id = ctx.task.id
        task_repo = ctx.task.extras.get("repo", "")
        if merge and isinstance(task_repo, str) and task_repo:
            # PROP-056/057: the deliverable lives in another repo — the merge
            # status is checked THERE, not in the tracker checkout.
            return self._done_guard_in_task_repo(task_id, Path(task_repo))

        active = self._load_active(task_id)
        if active is not None:
            self._guard_not_inside(ctx, active.worktree_path)
            if (
                not merge
                and ctx.event.to_state == "cancelled"
                and self._dirty(active.worktree_path)
            ):
                # PROP-084: a destructive terminal must not silently eat
                # uncommitted work — keep the tree and say so.
                return Delta(
                    work_log=(
                        "Worktree preserved: uncommitted changes "
                        f"at {active.worktree_path} (cancelled) — discard manually",
                    )
                )
            self._publish_pre_destroy(ctx, "worktree-merge" if merge else "worktree-discard")

        outcome = self._effects.teardown(task_id, merge=merge, force=ctx.force)
        if outcome is not None:
            return Delta(work_log=(f"Worktree {outcome}",))
        return None

    def _done_guard_in_task_repo(self, task_id: str, repo: Path) -> Delta | None:
        from ai_hats_wt import WorktreeManager, WorktreeStateLostError

        branch = f"task/{task_id.lower()}"
        if not WorktreeManager.branch_exists(repo, branch):
            return None  # nothing outstanding in the task repo
        if WorktreeManager.branch_merged_into_canonical_base(repo, branch) is None:
            raise WorktreeStateLostError(task_id, branch)  # genuinely un-merged there
        WorktreeManager.delete_merged_branch(repo, branch)
        return Delta(work_log=(f"Worktree merged (task repo {repo})",))

    def _load_active(self, task_id: str):
        from ai_hats_wt import WorktreeManager

        return WorktreeManager.load_for_task(
            self.project_dir, task_id, state_dir=worktrees_dir(self.project_dir)
        )

    def _guard_not_inside(self, ctx: DispatchContext, wt_path: Path) -> None:
        """HATS-788: never destroy the tree the caller is standing in."""
        try:
            cwd = ctx.caller_cwd.resolve()
            target = wt_path.resolve()
        except OSError:
            return
        if cwd == target or target in cwd.parents:
            raise AbortOperation(
                f"refusing '{ctx.event.to_state}' from inside the task's linked worktree "
                f"{wt_path} — cd to the main checkout ({self.project_dir}) and retry"
            )

    def _publish_pre_destroy(self, ctx: DispatchContext, operation: str) -> None:
        # PROP-047: blocking subscribers may abort or extract state before
        # the irreversible teardown; an abort propagates and cancels it.
        if self._kernel is None:
            return
        self._kernel.publish(
            PreDestroyEvent(operation=operation, task_id=ctx.task.id),
            actor=ctx.actor,
            caller_cwd=ctx.caller_cwd,
            force=ctx.force,
            reason=ctx.reason,
        )

    @staticmethod
    def _dirty(wt_path: Path) -> bool:
        try:
            out = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["git", "status", "--porcelain"],  # noqa: S607 — git from PATH, as everywhere
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                env=scrubbed_git_env(),  # HATS-890: never inherit ambient GIT_DIR
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False  # can't tell → keep the old discard behaviour
        return out.returncode == 0 and bool(out.stdout.strip())


def build_rack_kernel(
    project_dir: Path,
    *,
    tasks_dir: Path | None = None,
    state_md_path: Path | None = None,
    prefix: str = "HATS",
    sections: tuple[Section, ...] | None = None,
    worktree_effects: WtWorktreeEffects | None = None,
    journal_sink: JournalSink | None = None,
    lock_timeout: float | None = None,
    links_registry: LinksRegistry | None = None,
    extra_subscribers: Sequence = (),
) -> Kernel:
    """Assemble the integrator kernel: K1 core + every K3 stock extension
    (mirror of ``cli/_helpers._task_manager`` for the rack stack).

    ``sections=None`` (the default) resolves to the stock catalog extended by
    the materialized consumer config (HATS-1023) — scaffold and gate read one
    catalog, so contract and enforcement cannot drift (HATS-635)."""
    if tasks_dir is None or state_md_path is None:
        from .tracker_wiring import tracker_paths

        paths = tracker_paths(project_dir)
        tasks_dir = tasks_dir if tasks_dir is not None else paths.tasks_dir
        state_md_path = state_md_path if state_md_path is not None else paths.state_md_path
    if sections is None:
        from .rack_consumers import consumer_plan_sections

        sections = consumer_plan_sections(project_dir)

    # One backlog definition (catalog backlog.yaml or the packaged default)
    # feeds the kernel AND every subscriber — a single source, no diverging
    # default-load (HATS-1042, ADR-0017 §1). ``project_dir`` fails a legacy
    # project-root links.yaml closed, identically to the read path (R6).
    defn = resolve_definition(tasks_dir, prefix_alias=prefix, project_dir=project_dir)
    topology = defn.topology
    if links_registry is None:
        links_registry = defn.links_registry
    registry = tasks_dir.parent / "ownership.json"
    worktree = WorktreeExtension(project_dir, effects=worktree_effects, topology=topology)
    automation = EpicAutomationExtension(topology=topology, registry=links_registry)
    subscribers = [
        OwnershipSingleSlot(registry, topology=topology),
        # priority 8: evidence integrity refuses before the plan-gate (HATS-1031)
        FrozenIntegrityExtension(tasks_dir, topology=topology),
        PlanGateExtension(tasks_dir, sections, topology=topology),
        OwnershipClaim(registry, topology=topology),
        PlanScaffoldExtension(tasks_dir, sections, topology=topology),
        worktree,
        OwnershipRelease(registry, topology=topology),
        automation,
        DerivedViewsExtension(tasks_dir, state_md_path, topology=topology),
        *extra_subscribers,  # consumer add-ons (pre-destroy guards, K4 hook-runner)
    ]
    kwargs: dict = {}
    if lock_timeout is not None:
        kwargs["lock_timeout"] = lock_timeout
    kernel = Kernel(
        tasks_dir,
        prefix=defn.prefix,
        topology=topology,
        registry=links_registry,
        edge_names=defn.edge_names,
        subscribers=subscribers,
        journal_sink=journal_sink,
        **kwargs,
    )
    worktree.bind(kernel)
    automation.bind(kernel)
    return kernel


__all__ = [
    "OwnershipClaim",
    "OwnershipRelease",
    "OwnershipSingleSlot",
    "WorktreeExtension",
    "build_rack_kernel",
]
