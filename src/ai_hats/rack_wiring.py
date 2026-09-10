"""Integrator-side rack adapters: ownership + worktree extensions and the
kernel factory (HATS-1022, epic HATS-1014 K3).

The rack never imports the integrator (import-hygiene pin); THIS module is
the one-directional binding of the rack dispatcher to the production
ownership registry and the wt engine. ``build_rack_kernel`` preserves the
tracker's side-effect order (HATS-1260: its legacy-CLI mirror is gone):
single-slot guard → plan-gate → claim → worktree; teardown → release.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import Sequence

from ai_hats_rack import Kernel
from ai_hats_rack.composition import (
    bind_subscribers,
    build_bound_subscribers,
    build_card_schema,
    build_extensions,
    build_link_subscribers,
    stock_factories,
    stock_validators,
    validate_requires_states,
)
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
    Section,
)
from ai_hats_rack.selectors import ANY, EVERYWHERE, Selector
from ai_hats_core import scrubbed_git_env
from ai_hats_core.deadline import Deadline

from . import ownership
from .constants import ENV_ROOT_PID
from .session_identity import SessionIdentity, SessionIdentityError
from .wt_effects import WtWorktreeEffects

TERMINAL_STATES = ("done", "failed", "cancelled")

# HATS-1015 worktree liveness budget (ADR-0017 §4): a generous per-git ceiling so
# a hung worktree shell-out can't hold the task lock forever — kill → in-lock error
# → abort + journal (existing path). Config-overridable via WorktreeExtension(budget=).
WORKTREE_BUDGET = 60.0


def _rack_lock_deadline(ctx: DispatchContext) -> Deadline | None:
    """The kernel's task-lock instant as a budget (HATS-1603).

    The rack publishes a bare float — it is built without ai-hats-core, so it
    cannot mint the type. Binding the two is this module's job, and doing it
    here means the comparison stays in ``Deadline`` instead of at a call site.
    """
    if ctx.lock_expires_at is None:
        return None
    return Deadline(ctx.lock_expires_at, "rack task lock")


# The three hand-rolled products these replaced (`_exact`, `_edges_into`,
# `_edges_leaving_execute_or_terminal`) each rebuilt the topology's state product
# to say what one selector says — which is why every subscriber below had to be
# handed a topology it never read for any other purpose.


def _into(state: str) -> Selector:
    """Every road INTO ``state`` — the reclaim self-loop included."""
    return Selector(ANY, state)


def _out_of(state: str) -> Selector:
    """Every road OUT of ``state``. Wider than the product it replaced by exactly
    the self-loop, which the old helper subtracted by hand; the answer to that is
    §3.6 of the design — bind wide, filter inside (see ``OwnershipRelease``)."""
    return Selector(state, ANY)


def _session_id() -> str:
    """The launching session's id, or ``""`` outside one (HATS-1613).

    Through the identity, not the scalar beside it: a torn envelope read as
    absence would disarm the single-slot guard silently, and two live agents
    then share a slot. Single-slot runs first on every edge, so the refusal
    always lands where nothing has been written yet.
    """
    try:
        identity = SessionIdentity.from_env()
    except SessionIdentityError as exc:
        raise AbortOperation(f"ownership cannot name this session: {exc}") from exc
    return identity.id if identity is not None else ""


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

    def __init__(self, registry_path: Path, *, priority: int = 5) -> None:
        self.registry_path = registry_path
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [Subscription(EVERYWHERE, Phase.IN_LOCK, self._priority)]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        session_id = _session_id()
        if not session_id or ctx.is_epic:  # epics are trackers
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

    def __init__(self, registry_path: Path, *, priority: int = 20) -> None:
        self.registry_path = registry_path
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [Subscription(_into("execute"), Phase.IN_LOCK, self._priority)]

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
        priority: int = 40,
        epicify_priority: int = 10,
    ) -> None:
        self.registry_path = registry_path
        self._priority = priority
        self._epicify_priority = epicify_priority

    def subscriptions(self) -> Sequence[Subscription]:
        subs = [Subscription(_out_of("execute"), Phase.IN_LOCK, self._priority)]
        subs.extend(
            Subscription(_into(state), Phase.IN_LOCK, self._priority) for state in TERMINAL_STATES
        )
        subs.append(Subscription("epicify", Phase.POST_LOCK, self._epicify_priority))
        return subs

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if isinstance(ctx.event, EpicifyEvent):
            # A task that gained a child is a tracker now — drop its hold.
            ownership.finish(self.registry_path, ctx.event.epic_id)
            return None
        if (
            isinstance(ctx.event, EdgeEvent)
            and ctx.event.from_state == ctx.event.to_state == "execute"
        ):
            # `execute->` is every road OUT, and the reclaim self-loop is not one:
            # the claim at 20 takes the hold and this at 40 would drop it again,
            # leaving the card owned by nobody (HATS-1720, design.md §6.4). A
            # selector has no subtraction operator, so the accepted answer is to
            # bind wide and filter here — for exactly the pair the old product
            # subtracted, and no other. A DECLARED terminal self-loop still
            # releases: it arrives through `->done` and it always did, and skipping
            # it strands the session, since the teardown next door has no self-loop
            # guard and destroys the worktree while the hold survives.
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
        layout: ProjectLayout,
        *,
        effects: WtWorktreeEffects | None = None,
        setup_priority: int = 30,
        teardown_priority: int = 30,
        epicify_priority: int = 20,
        budget: float = WORKTREE_BUDGET,
    ) -> None:
        self.layout = layout
        self.project_dir = layout.root
        self._budget = budget
        self._effects = (
            effects if effects is not None else WtWorktreeEffects(layout, git_timeout=budget)
        )
        self._setup_priority = setup_priority
        self._teardown_priority = teardown_priority
        self._epicify_priority = epicify_priority
        self._kernel: Kernel | None = None

    def bind(self, kernel: Kernel) -> None:
        """Late-bound kernel handle for publishing pre-destroy events."""
        self._kernel = kernel

    def subscriptions(self) -> Sequence[Subscription]:
        subs = [Subscription(_into("execute"), Phase.IN_LOCK, self._setup_priority)]
        subs.extend(
            Subscription(_into(state), Phase.IN_LOCK, self._teardown_priority)
            for state in TERMINAL_STATES
        )
        subs.append(Subscription("epicify", Phase.POST_LOCK, self._epicify_priority))
        return subs

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if isinstance(ctx.event, EpicifyEvent):
            # Reclaim the now-epic parent's worktree iff empty/merged.
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
            return None  # epics never get a worktree
        if ctx.event.from_state == "done":
            return None  # reopen: the operator owns the worktree decision
        if ctx.force:
            # Force relaxes the FSM arrow, NOT the canonical-base
            # contract — the guard must run explicitly on the force path.
            self._effects.assert_canonical_base()
            # A forced execute is a manual state correction — no
            # fresh worktree (one spun off HEAD orphaned retro work, PROX-287).
            return Delta(work_log=("Forced → execute: no worktree created (manual override)",))
        wt_path = self._effects.setup(
            ctx.task.id,
            ctx.task.role,
            caller_cwd=ctx.caller_cwd,
            outer_deadline=_rack_lock_deadline(ctx),
        )
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
                and self._dirty(active.worktree_path, self._budget)
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

        outcome = self._effects.teardown(
            task_id, merge=merge, force=ctx.force, outer_deadline=_rack_lock_deadline(ctx)
        )
        if outcome is not None:
            return Delta(work_log=(f"Worktree {outcome}",))
        return None

    def _done_guard_in_task_repo(self, task_id: str, repo: Path) -> Delta | None:
        from ai_hats_wt import WorktreeManager, WorktreeStateLostError

        branch = f"task/{task_id.lower()}"
        if not WorktreeManager.branch_exists(repo, branch, timeout=self._budget):
            return None  # nothing outstanding in the task repo
        if (
            WorktreeManager.branch_merged_into_canonical_base(repo, branch, timeout=self._budget)
            is None
        ):
            raise WorktreeStateLostError(task_id, branch)  # genuinely un-merged there
        WorktreeManager.delete_merged_branch(repo, branch, timeout=self._budget)
        return Delta(work_log=(f"Worktree merged (task repo {repo})",))

    def _load_active(self, task_id: str):
        from ai_hats_wt import WorktreeManager

        return WorktreeManager.load_for_task(
            self.project_dir,
            task_id,
            state_dir=self.layout.sessions.worktrees,
            git_timeout=self._budget,
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
            lock_expires_at=ctx.lock_expires_at,  # Still in-lock here
        )

    @staticmethod
    def _dirty(wt_path: Path, budget: float = WORKTREE_BUDGET) -> bool:
        try:
            out = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["git", "status", "--porcelain"],  # noqa: S607 — git from PATH, as everywhere
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                env=scrubbed_git_env(),  # Never inherit ambient GIT_DIR
                timeout=budget,
            )
        except (OSError, subprocess.SubprocessError):
            return False  # can't tell (incl. timeout) → keep the old discard behaviour
        return out.returncode == 0 and bool(out.stdout.strip())


def build_rack_kernel(
    layout: ProjectLayout,
    *,
    backlog_owner: ProjectLayout | None,
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
    """Assemble the integrator kernel: K1 core + every K3 stock extension.

    ``sections=None`` (the default) resolves to the stock ``DEFAULT_PLAN_SECTIONS``
    catalog via ``stock_factories`` — scaffold and gate read one catalog, so
    contract and enforcement cannot drift (HATS-635)."""
    if tasks_dir is None or state_md_path is None:
        from .tracker_wiring import tracker_paths

        paths = tracker_paths(layout)
        tasks_dir = tasks_dir if tasks_dir is not None else paths.tasks_dir
        state_md_path = state_md_path if state_md_path is not None else paths.state_md_path

    # One definition feeds the kernel AND every subscriber (HATS-1042, ADR-0017
    # §1). The legacy links.yaml is the OWNER's; with no owner the
    # anchor is still probed, so R6 never gets weaker than it was.
    defn = resolve_definition(
        tasks_dir, prefix_alias=prefix, project_dir=(backlog_owner or layout).root
    )
    topology = defn.topology
    if links_registry is None:
        links_registry = defn.links_registry
    registry = tasks_dir.parent / "ownership.json"
    worktree = WorktreeExtension(layout, effects=worktree_effects)
    automation = EpicAutomationExtension(topology=topology, registry=links_registry)
    # Declaration channel: frozen-integrity (ambient) + scaffold/
    # plan-gate/stamp/clear (declaration-bound) come from the definition slots.
    # derived-views stays code-channel — it needs the STATE.md path (ADR-0017 §4).
    factories = stock_factories(sections)
    declared = (
        build_extensions(defn, tasks_dir, factories)
        + build_bound_subscribers(defn, tasks_dir, factories)
        + build_link_subscribers(defn, tasks_dir, factories)
    )
    # Code channel (integrator scope). Priorities give the single in-lock order —
    # single-slot(5) < frozen(8) < plan-gate(10) < hook(15) < claim(20) <
    # scaffold/worktree(30) < release(40); the list order only breaks ties.
    subscribers = [
        *declared,
        OwnershipSingleSlot(registry),
        OwnershipClaim(registry),
        worktree,
        OwnershipRelease(registry),
        automation,
        DerivedViewsExtension(tasks_dir, state_md_path, topology=topology),
        *extra_subscribers,  # consumer add-ons (pre-destroy guards, K4 hook-runner)
    ]
    # Fail-closed at composition: a subscriber's declared state vocabulary must
    # fit the topology (the HATS-692 stranding class, HATS-1043 R8).
    validate_requires_states(subscribers, topology, source=str(tasks_dir))
    kwargs: dict = {}
    if lock_timeout is not None:
        kwargs["lock_timeout"] = lock_timeout
    kernel = Kernel(
        tasks_dir,
        prefix=defn.prefix,
        topology=topology,
        registry=links_registry,
        edge_names=defn.edge_names,
        schema=build_card_schema(defn, stock_validators()),
        subscribers=subscribers,
        journal_sink=journal_sink,
        **kwargs,
    )
    bind_subscribers(subscribers, kernel)  # uniform bind loop (worktree, automation, add-ons)
    return kernel


__all__ = [
    "OwnershipClaim",
    "OwnershipRelease",
    "OwnershipSingleSlot",
    "WorktreeExtension",
    "build_rack_kernel",
]
