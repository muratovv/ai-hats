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

from ..dispatch import AbortOperation, Delta, DispatchContext, Phase
from .epic import AUTOMATION_ACTOR
from .sections import DEFAULT_PLAN_SECTIONS, Section, render_scaffold, unfilled_sections


class PlanConsentExtension:
    """Blocks ``plan → execute`` transition unless AI_HATS_PLAN_ACK=1 is set (in-lock)."""

    name = "plan-consent"
    PHASE = Phase.IN_LOCK

    def requires_states(self) -> frozenset[str]:
        return frozenset({"execute"})  # gates on entering execute

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        if ctx.actor == AUTOMATION_ACTOR or ctx.is_epic or ctx.force:
            return None  # epics, automation, and forced overrides skip consent check
        from_state = getattr(ctx.event, "from_state", "")
        if from_state == "plan" and os.environ.get("AI_HATS_PLAN_ACK") != "1":
            raise AbortOperation(
                f"Transition 'plan -> execute' for '{ctx.task.id}' requires supervisor approval. "
                "AI_HATS_PLAN_ACK=1 is not set in environment.\n"
                "1. Present plan.md to the supervisor in chat.\n"
                "2. After receiving explicit approval, re-run with:\n"
                f"   AI_HATS_PLAN_ACK=1 rack transition {ctx.task.id} execute"
            )
        return None



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
                "fill them before entering execute"
            )
        return None
