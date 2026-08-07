"""Consumer add-ons for the rack kernel: the ``checks:`` runner (HATS-1141).

Successor of the ``lifecycle_hooks`` executor retired in HATS-1147 (ADR-0019
D8): a binding declared by a trait or role fires on the FSM edge it names, in
the lock, before the single persist.

Resolution — which bytes a binding runs — lives in :mod:`ai_hats.check_resolve`.
What stays here is the dispatcher-facing shape and the outcome policy (ADR-0019
D4), which is per-binding, not the worktree channel's uniform fail-closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from ai_hats_core import ResolvedCheck
from ai_hats_rack.dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from ai_hats_rack.fsm import Topology, all_edge_keys
from ai_hats_rack.kernel import LOCK_TIMEOUT

from .check_resolve import CheckResolutionError, resolve_edge_checks, session_id
from .hook_exec import HookRun, HookVerdict, run_hook
from .libraries.models import CheckBindingError

#: Reserved "hook" slot of the in-lock ladder (``rack_wiring.build_rack_kernel``)
#: — after the plan-gate, before the ownership claim, so a refusal leaves neither
#: ownership nor a worktree. Explicit: list position only breaks ties.
CHECK_PRIORITY = 15

#: Per-check wall-clock budget. Strictly below the rack's own lock timeout so a
#: hung check is bounded by ITS timeout, not by the lock (ADR-0020 D2).
EDGE_CHECK_TIMEOUT_S: float = 20.0

if EDGE_CHECK_TIMEOUT_S >= LOCK_TIMEOUT:  # pragma: no cover — explicit raise survives -O
    raise RuntimeError(
        f"EDGE_CHECK_TIMEOUT_S ({EDGE_CHECK_TIMEOUT_S}) must be < LOCK_TIMEOUT ({LOCK_TIMEOUT})"
    )


class CheckRunnerExtension:
    """Runs every ``checks:`` binding declared for the edge being taken."""

    name = "checks"

    def __init__(
        self,
        project_dir: Path,
        *,
        tasks_dir: Path,
        topology: Topology,
        priority: int = CHECK_PRIORITY,
        timeout: float | None = None,
        resolve: Callable[[], tuple[ResolvedCheck, ...]] | None = None,
    ) -> None:
        self.project_dir = project_dir
        self._tasks_dir = tasks_dir
        self._topology = topology
        self._priority = priority
        self._timeout = EDGE_CHECK_TIMEOUT_S if timeout is None else timeout
        self._resolve = resolve if resolve is not None else self._resolve_bound

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in all_edge_keys(self._topology)
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        notes: list[str] = []
        for check in self._bound_to(ctx.event.key):
            run = run_hook(
                check.script_path,
                point=check.point,
                timeout=self._timeout,
                project_dir=self.project_dir,
                force=ctx.force,
                task_id=ctx.task.id,
                log_path=self._log_path(ctx.task.id, ctx.event.key),
            )
            if run.ok:
                continue
            if check.on_error == "warn" and run.downgradable:
                notes.append(_downgraded(check, run))
                continue
            raise AbortOperation(_refusal(check, run))
        return Delta(work_log=tuple(notes)) if notes else None

    def _resolve_bound(self) -> tuple[ResolvedCheck, ...]:
        return resolve_edge_checks(
            self.project_dir, topology=self._topology, session_id=session_id()
        )

    def _bound_to(self, event_key: str) -> tuple[ResolvedCheck, ...]:
        """R8: resolution failures become this channel's own typed refusal —
        a traceback out of an in-lock subscriber is a defect, not a message."""
        try:
            resolved = self._resolve()
        except (CheckBindingError, CheckResolutionError, OSError) as exc:
            raise AbortOperation(f"checks: {exc}") from exc
        return tuple(check for check in resolved if check.point == event_key)

    def _log_path(self, task_id: str, event_key: str) -> Path:
        """R3.4. The dot-component keeps the log out of the document registry, so
        a check's output never gets pinned into ``rack context``."""
        return self._tasks_dir / task_id / ".checks" / f"{event_key.replace(':', '-')}.log"


def _binding(check: ResolvedCheck) -> str:
    return f"{check.declared_by!r} binds {check.skill}/{check.script} on {check.point}"


def _refusal(check: ResolvedCheck, run: HookRun) -> str:
    """A refusal that spoke stands alone — R3.3 wants the child's tail verbatim
    in ``--json``. Everything else is the substrate failing, so it is named."""
    if run.verdict is HookVerdict.REFUSE:
        return run.reason
    return f"checks: {_binding(check)} — {run.reason}"


def _downgraded(check: ResolvedCheck, run: HookRun) -> str:
    return f"checks: {_binding(check)} broke, downgraded by on_error: warn — {_oneline(run.reason)}"


def _oneline(reason: str) -> str:
    return " / ".join(line.strip() for line in reason.splitlines() if line.strip())


def consumer_subscribers(
    project_dir: Path,
    *,
    tasks_dir: Path,
    topology: Topology,
) -> list:
    """The consumer add-on pack for ``build_rack_kernel(extra_subscribers=…)``.

    ``topology`` is the one the kernel actually runs (``resolve_definition``),
    never a re-opened default — that divergence is what R7 makes loud.
    """
    return [CheckRunnerExtension(project_dir, tasks_dir=tasks_dir, topology=topology)]


__all__ = [
    "CHECK_PRIORITY",
    "EDGE_CHECK_TIMEOUT_S",
    "CheckRunnerExtension",
    "consumer_subscribers",
]
