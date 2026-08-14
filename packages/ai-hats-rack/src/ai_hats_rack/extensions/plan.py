"""Stock plan handlers: scaffold on ENTERING ``plan``, per-section gate on
ENTERING ``execute`` (HATS-1022; heirs of HATS-635/621/794/328).

Declaration-bound (HATS-1043, ADR-0017 §3): the loader binds them from the
``on_enter`` slots — they hardcode no event keys. Reopen is exempted by the
declarative ``skip: [plan-gate]`` on the reopen edge, not a code filter. Both
read one section catalog (``sections.py``) so template and checklist cannot drift.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, Sequence

from ..dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from .epic import AUTOMATION_ACTOR
from .sections import DEFAULT_PLAN_SECTIONS, Section, render_scaffold, unfilled_sections

#: The edge the ticket pays for. Named once: the gate and its spender must not
#: drift into checking one move and settling another.
CONSENT_EDGE = "edge:plan--execute"


class TicketStore(Protocol):
    """The consent-ticket seam the integrator fills (HATS-1642).

    Two acts, deliberately apart: ``peek`` answers whether consent is on hand,
    ``spend`` uses it up. The rack may not import the library that writes the
    ticket, so both arrive through the factory.
    """

    def peek(self, task_id: str) -> bool: ...

    def spend(self, task_id: str) -> bool: ...


class PlanConsentExtension:
    """Blocks ``plan → execute`` until the supervisor consents (in-lock).

    Two channels: a one-shot ticket the guard minted when the supervisor answered
    in chat, and ``AI_HATS_PLAN_ACK=1`` in the launching environment wherever no
    question can be asked. The ticket is only CHECKED here, so the refusal stays
    early; spending it is :meth:`spender`'s, post-lock — subscribers after this
    one can still abort, and an eaten click cannot be given again.
    """

    name = "plan-consent"
    PHASE = Phase.IN_LOCK

    def __init__(self, tickets: TicketStore | None = None) -> None:
        self._tickets = tickets
        self._accepted: set[str] = set()

    def requires_states(self) -> frozenset[str]:
        return frozenset({"execute"})  # gates on entering execute

    def spender(self, *, priority: int = 15) -> "PlanConsentSpend":
        """The post-lock half — see the class docstring."""
        return PlanConsentSpend(self, priority=priority)

    def settle(self, task_id: str) -> str:
        """Spend the ticket this transition rode in on; a note when it could not."""
        if task_id not in self._accepted:
            return ""
        self._accepted.discard(task_id)
        if self._tickets is None or self._tickets.spend(task_id):
            return ""
        return f"consent ticket for {task_id} could not be spent — it may still be on disk"

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if ctx.actor == AUTOMATION_ACTOR or ctx.is_epic or ctx.force:
            return None  # epics, automation, and forced overrides skip consent check
        if getattr(ctx.event, "from_state", "") != "plan":
            return None
        self._accepted.discard(ctx.task.id)  # a prior attempt that never settled
        if os.environ.get("AI_HATS_PLAN_ACK") == "1":
            return None
        if self._tickets is not None and self._tickets.peek(ctx.task.id):
            self._accepted.add(ctx.task.id)
            return Delta(work_log=("plan → execute: supervisor consent ticket accepted",))
        raise AbortOperation(
            f"Transition 'plan -> execute' for '{ctx.task.id}' requires supervisor approval, "
            "and none has arrived.\n"
            "1. Present plan.md to the supervisor in chat and STOP.\n"
            "2. Then re-run this exact command: the guard turns it into a one-click\n"
            "   question in chat, and the supervisor's answer is what carries consent.\n"
            "3. Where there is nobody to ask — headless, cron, a surface without\n"
            "   runtime hooks — consent comes from the launching environment, on its\n"
            "   own line, before the session starts:\n"
            "     export AI_HATS_PLAN_ACK=1"
        )


class PlanConsentSpend:
    """Post-lock half of ``plan-consent``: the ticket is spent once the
    transition has actually happened (HATS-1642). Code-channel, not declared —
    the declaration binds a handler to ONE phase, and this is the other one."""

    name = "plan-consent-spend"

    def __init__(self, gate: PlanConsentExtension, *, priority: int = 15) -> None:
        self._gate = gate
        self._priority = priority

    def requires_states(self) -> frozenset[str]:
        return frozenset({"plan", "execute"})

    def subscriptions(self) -> Sequence[Subscription]:
        return [Subscription(CONSENT_EDGE, Phase.POST_LOCK, self._priority)]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        note = self._gate.settle(ctx.task.id)
        return Delta(work_log=(note,)) if note else None


class PlanScaffoldExtension:
    """Writes the plan.md scaffold on entering ``plan`` (in-lock).

    Idempotent: an existing plan.md is preserved and noted in the work_log
    (supervisor decision, epic HATS-1014). Writes the file directly —
    fs-as-truth, no doc-store API (K2 owns that surface).
    """

    name = "plan-scaffold"
    PHASE = Phase.IN_LOCK

    def __init__(
        self,
        tasks_dir: Path,
        sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.sections = sections

    def requires_states(self) -> frozenset[str]:
        return frozenset({"plan"})  # scaffolds on entering plan

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if ctx.actor == AUTOMATION_ACTOR:
            return None  # epic auto-hops never scaffold (parity: old auto-path)
        plan_path = self.tasks_dir / ctx.task.id / "plan.md"
        if plan_path.exists():
            return Delta(work_log=("plan.md already exists — preserved",))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            render_scaffold(self.sections).format(task_id=ctx.task.id, title=ctx.task.title),
            encoding="utf-8",
        )
        return None


class PlanGateExtension:
    """Blocks ``→ execute`` while required plan sections are empty (in-lock).

    The abort reason NAMES every empty required section (HATS-635); epics are
    never gated — a tracker, not a unit of executable work (HATS-794); reopen
    ``done → execute`` is not gated (HATS-328) via the declarative ``skip``.
    """

    name = "plan-gate"
    PHASE = Phase.IN_LOCK

    def __init__(
        self,
        tasks_dir: Path,
        sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.sections = sections

    def requires_states(self) -> frozenset[str]:
        return frozenset({"execute"})  # gates on entering execute

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if ctx.actor == AUTOMATION_ACTOR:
            return None  # epic auto-hops carry no gate semantics
        if ctx.is_epic:
            # HATS-794: pure state flip; the note keeps the card auditable.
            return Delta(work_log=("Epic → execute (tracker): no plan-gate, no worktree",))
        plan_path = self.tasks_dir / ctx.task.id / "plan.md"
        try:
            text: str | None = plan_path.read_text(encoding="utf-8")
        except OSError:
            text = None
        unfilled = unfilled_sections(text, self.sections)
        if unfilled:
            raise AbortOperation(
                f"Empty required section(s) in {plan_path}: {', '.join(unfilled)} — "
                "fill them before entering execute" + self._spent_question(ctx)
            )
        return None

    @staticmethod
    def _spent_question(ctx: DispatchContext) -> str:
        """`plan → execute` raises a consent prompt BEFORE the plan is read, so a
        refusal here means the supervisor answered for a move that did not
        happen. Saying so beats a second plan-gate inside the hook (HATS-1642)."""
        if getattr(ctx.event, "from_state", "") != "plan":
            return ""
        return (
            ".\nThe consent prompt for this move goes up before the plan is read, so an "
            "answer already given was spent on a transition that did not happen — the "
            "next attempt is asked again."
        )
