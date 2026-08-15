"""The carrier side of the binding channel for the rack (``composition.apps.rack``).

Successor of the ``lifecycle_hooks`` executor retired in HATS-1147 (ADR-0019
D8): a binding declared by a trait or role fires on the FSM edge it names, in
the lock, before the single persist.

HATS-1541 (ADR-0019 D11) moved the *decisions* out. What ``edge:`` means, which
of the carried rows this instance's topology has an edge for, and what the
subscriptions are is now ``ai_hats_rack.checks``; what stays here is what only
the integrator can do — compose the role, resolve the script
(:mod:`ai_hats.check_resolve`) and spawn the process, which the rack may not
(``subprocess`` is forbidden in it by an AST import pin).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Sequence

from ai_hats_core import ConsentPoint, ResolvedCheck
from ai_hats_core.deadline import Deadline
from ai_hats_rack.checks import (
    CHECK_PRIORITY,
    CONSENT_ROW,
    EDGE_CHECK_TIMEOUT_S,
    CheckDeclaration,
    CheckOutcome,
    CheckPortFactory,
    CheckRequest,
    check_subscriber,
)
from ai_hats_rack.definition import BacklogDefinition
from ai_hats_rack.dispatch import AbortOperation

from .check_points import check_failure_reason, check_log_token
from .check_resolve import CheckResolutionError, resolve_carried_rows, session_identity_for
from .hook_exec import run_hook
from .libraries.models import CheckBindingError


def _shipped_deadline(request: CheckRequest) -> Deadline:
    """The ceiling a check runs under (HATS-1603).

    The rack forbids itself a core dependency, so it ships the moment its task
    lock expires and the type is minted here. Without one — a road that holds no
    task lock — the check is bounded by its own budget, declared as such.
    """
    if request.lock_expires_at is None:
        return Deadline.without_lock(request.timeout, why="rack check, no task lock")
    return Deadline(request.lock_expires_at, "rack task lock")


class AiHatsCheckPort:
    """``ai_hats_rack.checks.CheckPort``: where rows come from, and who runs one.

    The rack holds the deadline (``LOCK_TIMEOUT`` is its constant) and ships the
    budget in the request, so the two sides cannot keep constants that drift.
    """

    #: The app key this integration collects. Named HERE, by the module that
    #: integrates the rack — the composition core knows no application's name.
    APP = "rack"

    def __init__(
        self,
        backlog_owner: Path | None,
        *,
        catalog: Path,
        resolve: Callable[[], tuple[ResolvedCheck, ...]] | None = None,
        resolve_consent: Callable[[], tuple[ConsentPoint, ...]] | None = None,
    ) -> None:
        #: The backlog's own project — the ONLY one this channel knows (HATS-1573):
        #: it composes the role, it is the gate's cwd, and it is the
        #: ``AI_HATS_PROJECT_DIR`` the gate reads as "this project".
        self.backlog_owner = backlog_owner
        # The catalog of the backlog being gated, not the project's tasks dir:
        # a sibling's check log belongs under the sibling, and the gate reads
        # AI_HATS_TASKS_DIR to decide whether the backlog is its business at all.
        self._catalog = catalog
        self._resolve = resolve
        self._resolve_consent = resolve_consent
        self._worktrees: dict[str, Path | None] = {}

    def check_declarations(self) -> Sequence[CheckDeclaration]:
        """Every carried row, already deduped, provenance-tagged and rooted.

        Two kinds since HATS-1682: a gate row, and a consent row that spawns
        nothing. Both travel, because the rack is the only party holding the
        topology that can tell a misspelt point from one aimed at a sibling —
        and before this, a consent row reached no validator at all (A5).

        Resolution failures become this channel's own typed refusal — a
        traceback out of an in-lock subscriber is a defect, not a message.
        """
        try:
            resolved, consent = self._carried()
        except (CheckBindingError, CheckResolutionError, OSError) as exc:
            raise AbortOperation(f"checks: {exc}") from exc
        return (
            *(_declaration(check) for check in resolved),
            *(_consent_declaration(point) for point in consent),
        )

    def _carried(self) -> tuple[tuple[ResolvedCheck, ...], tuple[ConsentPoint, ...]]:
        """Both kinds, from ONE composition — or from whoever was injected.

        An injected source stands in for the composition on its own side: pairing
        a fake row list with a live consent compose would make the fake compose
        the real project underneath it.
        """
        if self._resolve is not None or self._resolve_consent is not None:
            return (
                tuple(self._resolve()) if self._resolve is not None else (),
                tuple(self._resolve_consent()) if self._resolve_consent is not None else (),
            )
        if self.backlog_owner is None:
            # A backlog nobody owns declares nothing, so nothing fires. Said out
            # loud: a gate that is not there must not read as a gate that passed.
            print(
                f"checks: no project owns the backlog at {self._catalog} — no role "
                f"composes onto it, so no bound check runs on this transition",
                file=sys.stderr,
            )
            return (), ()
        return resolve_carried_rows(
            self.backlog_owner,
            self.APP,
            # Scoped to the backlog's owner: unscoped, another project's session
            # chose the rows, and one declaring none closed the card ungated
            # (HATS-1631).
            identity=session_identity_for(self.backlog_owner),
        )

    def _project(self) -> Path:
        """The owner, proven present: rows only exist when a project declared them."""
        if self.backlog_owner is None:
            raise CheckResolutionError(
                f"checks: a bound check was requested for the backlog at {self._catalog}, "
                f"which no project owns — nothing could have declared it"
            )
        return self.backlog_owner

    def run_check(self, request: CheckRequest) -> CheckOutcome:
        check: ResolvedCheck = request.declaration.handle
        run = run_hook(
            check.script_path,
            point=request.event,
            budget=request.timeout,
            deadline=_shipped_deadline(request),
            project_dir=self._project(),
            force=request.force,
            task_id=request.task_id,
            worktree_path=self._worktree_path(request.task_id),
            tasks_dir=self._catalog,
            log_path=self._log_path(request.task_id, check, request.event),
        )
        return CheckOutcome(
            ok=run.ok,
            reason=check_failure_reason(check, run),
            downgradable=run.downgradable,
        )

    def _worktree_path(self, task_id: str) -> Path | None:
        """The task's live worktree, resolved ONCE for every binding on the edge.

        HATS-1540 R2: before this, each gate re-derived
        ``<ai_hats_dir>/sessions/worktrees/task-<id>.json`` and parsed the JSON
        by hand — three spellings of one lookup, and a gate that got it wrong
        judged the wrong tree. A pure read (``peek_worktree_path``), because a
        refused transition must leave lifecycle state exactly as it found it.
        The memo keeps that "once" now that the rack calls back per row.
        """  # comment-length: allow — why it is cached is the HATS-1540 contract
        if task_id not in self._worktrees:
            self._worktrees[task_id] = self._lookup_worktree(task_id)
        return self._worktrees[task_id]

    def _lookup_worktree(self, task_id: str) -> Path | None:
        """The read itself — memoized by the caller, so it takes its filelock once."""
        from ai_hats_wt import WorktreeManager

        from .paths import worktrees_dir

        try:
            project = self._project()
            path = WorktreeManager.peek_worktree_path(
                project, task_id, state_dir=worktrees_dir(project)
            )
        except (OSError, ValueError) as exc:
            # "Cannot tell" is not "no worktree". Handing a gate an absent
            # variable here reads to it as "this card brings no commits, nothing
            # to gate" — the wave-through the channel exists to remove.
            raise AbortOperation(
                f"checks: the worktree of {task_id} could not be resolved "
                f"({type(exc).__name__}): {exc} — refusing rather than gating no tree"
            ) from exc
        return path

    def _log_path(self, task_id: str, check: ResolvedCheck, event: str) -> Path:
        """R3.4. The dot-component keeps the log out of the document registry, so
        a check's output never gets pinned into ``rack context``.

        One file per (task, point, binding). ``run_hook`` truncates the log it is
        handed, so a name built from the point alone let the second binding on an
        edge wipe the first one's file — and ``_note_truncation`` went on
        pointing the first one's reason at it (HATS-1137). The discriminator is
        the dedup identity ``check_points.resolve_checks`` keys on, so a retry
        of the same edge still lands on that binding's own previous log.
        """  # comment-length: allow — the collision recurred once already
        name = f"{event.replace(':', '-') or 'event'}~{check_log_token(check)}.log"
        return self._catalog / task_id / ".checks" / name


def _declaration(check: ResolvedCheck) -> CheckDeclaration:
    """One resolved row as the rack sees it: a point, a policy, a label, a handle.

    ``handle`` is the ``ResolvedCheck`` itself and travels back untouched — the
    rack never opens it, which is what keeps ``{skill, script, script_path}``
    out of a package that must not know them.
    """
    return CheckDeclaration(
        path=check.path,
        at=check.at,
        cargo=check.cargo,
        on_error=check.on_error,
        label=_binding(check),
        handle=check,
    )


def _consent_declaration(point: ConsentPoint) -> CheckDeclaration:
    """One declared consent point as the rack sees it (HATS-1682).

    ``on_error`` is empty and not ``refuse``: this row spawns nothing, so it has
    no verdict and no failure policy. Nothing reads it — the subscriber drops
    the kind before ``run_check`` — and calling it ``refuse`` would put a policy
    on the report for a row that can never fail.
    """
    return CheckDeclaration(
        path=point.path,
        at=(point.point,),
        cargo={},
        on_error="",
        label=f"{point.declared_by!r} declares consent under apps.{point.app}",
        handle=point,
        kind=CONSENT_ROW,
    )


def _binding(check: ResolvedCheck) -> str:
    return f"{check.declared_by!r} binds {check.run} under apps.{check.app}"


def check_port_factory(backlog_owner: Path | None) -> CheckPortFactory:
    """This integrator's ``CheckPortFactory``: one executor per gated catalog.

    The whole of what ai-hats contributes to the channel since HATS-1575. Which
    topology a row is matched against, which selectors a backlog answers to and
    which instances get a subscriber at all are the rack's to decide, and it
    decides them from the definition it runs (``ai_hats_rack.checks``); deriving
    them here meant every road that did not repeat the derivation — the sibling
    backlogs, the workspace the reflect consumers mount — silently had no gate.
    """  # comment-length: allow — the boundary this draws IS the fix
    return lambda catalog: AiHatsCheckPort(backlog_owner, catalog=catalog)


def consumer_subscribers(
    backlog_owner: Path | None,
    *,
    definition: BacklogDefinition,
    catalog: Path,
    known_backlogs: Sequence[str] = (),
) -> list:
    """The consumer add-on pack for ``build_rack_kernel(extra_subscribers=…)``.

    The tasks kernel is assembled outside :class:`Workspace`, so its subscriber
    is appended here — through the rack's own constructor, so the two roads
    cannot wire the same channel two ways.
    """
    return [
        check_subscriber(
            definition,
            port=check_port_factory(backlog_owner)(catalog),
            known_backlogs=known_backlogs,
        )
    ]


__all__ = [
    "CHECK_PRIORITY",
    "EDGE_CHECK_TIMEOUT_S",
    "AiHatsCheckPort",
    "check_port_factory",
    "consumer_subscribers",
]
