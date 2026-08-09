"""Consumer add-ons for the rack kernel: the ``checks:`` runner (HATS-1141).

Successor of the ``lifecycle_hooks`` executor retired in HATS-1147 (ADR-0019
D8): a binding declared by a trait or role fires on the FSM edge it names, in
the lock, before the single persist.

Resolution — which bytes a binding runs — lives in :mod:`ai_hats.check_resolve`.
What stays here is the dispatcher-facing shape and the outcome policy (ADR-0019
D4), which is per-binding, not the worktree channel's uniform fail-closed.
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Callable, Sequence

from ai_hats_core import ResolvedCheck
from ai_hats_rack.dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from ai_hats_rack.fsm import Topology, all_edge_keys
from ai_hats_rack.kernel import LOCK_TIMEOUT

from .check_resolve import CheckResolutionError, resolve_edge_checks, session_id
from .hook_exec import HookRun, HookVerdict, run_hook
from .libraries.models import CheckBindingError, resolve_namespace

#: The tasks dir this transition runs against (HATS-1540 R4). Role scope is not
#: backlog scope: one role fires on every backlog the rack CLI touches, including
#: a scratch ``--tasks-dir``, so without this a script cannot tell "not my card"
#: from "``AI_HATS_DIR`` leaked". The prefix is derivable from ``AI_HATS_TASK_ID``
#: and is deliberately not a second variable.
ENV_TASKS_DIR = "AI_HATS_TASKS_DIR"

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
        bound = self._bound_to(ctx.event.key)
        if not bound:
            return None
        worktree_path = self._worktree_path(ctx.task.id)
        for check in bound:
            run = run_hook(
                check.script_path,
                point=check.point,
                timeout=self._timeout,
                project_dir=self.project_dir,
                force=ctx.force,
                task_id=ctx.task.id,
                worktree_path=worktree_path,
                extra_env={ENV_TASKS_DIR: str(self._tasks_dir)},
                log_path=self._log_path(ctx.task.id, ctx.event.key, check),
            )
            if run.ok:
                continue
            if check.on_error == "warn" and run.downgradable:
                notes.append(_downgraded(check, run))
                continue
            raise AbortOperation(_refusal(check, run))
        return Delta(work_log=tuple(notes)) if notes else None

    def _worktree_path(self, task_id: str) -> Path | None:
        """The task's live worktree, resolved ONCE for every binding on the edge.

        HATS-1540 R2: before this, each gate re-derived
        ``<ai_hats_dir>/sessions/worktrees/task-<id>.json`` and parsed the JSON
        by hand — three spellings of one lookup, and a gate that got it wrong
        judged the wrong tree. A pure read (``peek_worktree_path``), because a
        refused transition must leave lifecycle state exactly as it found it.
        """
        from ai_hats_wt import WorktreeManager

        from .paths import worktrees_dir

        try:
            return WorktreeManager.peek_worktree_path(
                self.project_dir, task_id, state_dir=worktrees_dir(self.project_dir)
            )
        except OSError as exc:
            # Named, never swallowed: an unreadable state dir means the gate
            # would judge with no tree rather than with the wrong one, and
            # `run_hook` removes the variable so no stale ambient path survives.
            print(f"WARN: checks: could not resolve the worktree of {task_id}: {exc}", flush=True)
            return None

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

    def _log_path(self, task_id: str, event_key: str, check: ResolvedCheck) -> Path:
        """R3.4. The dot-component keeps the log out of the document registry, so
        a check's output never gets pinned into ``rack context``.

        One file per (task, edge, binding). ``run_hook`` truncates the log it is
        handed, so a name built from the edge alone let the second binding on an
        edge wipe the first one's file — and ``_note_truncation`` went on
        pointing the first one's reason at it (HATS-1137). The discriminator is
        the dedup identity ``check_points.resolve_checks`` keys on, so a retry
        of the same edge still lands on that binding's own previous log.
        """
        binding = f"{_escaped(resolve_namespace(check.skill))}~{_escaped(check.script)}"
        name = f"{event_key.replace(':', '-')}~{binding}.log"
        return self._tasks_dir / task_id / ".checks" / name


#: Characters a binding component keeps verbatim in a log name.
_LITERAL = frozenset(string.ascii_letters + string.digits + "._-")


def _escaped(part: str) -> str:
    """One binding component as a filename-safe token, REVERSIBLY.

    A skill name carries a namespace separator and a script is a relative path,
    so both must lose their slashes; replacing them would collapse ``a/b.sh``
    and ``a-b.sh`` onto one name, which is the truncation defect again. ``/``
    therefore becomes ``+`` (readable) and every other non-literal byte becomes
    ``%XX`` — including ``+`` and ``%`` themselves, so the mapping decodes and
    two different components can never produce the same token. ``~`` is
    non-literal too, which is what makes it a safe joiner.
    """  # comment-length: allow — why it escapes rather than replaces is the fix
    out = []
    for char in part:
        if char in _LITERAL:
            out.append(char)
        elif char == "/":
            out.append("+")
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode())
    return "".join(out)


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
    "ENV_TASKS_DIR",
    "CheckRunnerExtension",
    "consumer_subscribers",
]
