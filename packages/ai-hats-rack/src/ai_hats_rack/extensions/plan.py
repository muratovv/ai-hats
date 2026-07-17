"""Stock plan extensions: scaffold on ``*→plan``, per-section gate on
``*→execute`` (HATS-1022; heirs of HATS-635/621/794/328).

Both read one section catalog (``sections.py``) so the template the agent
fills and the checklist the gate enforces cannot drift. First "users" of the
extension API (epic HATS-1014 §2.3): attached by default, removable, never
in the kernel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from ..fsm import Topology, load_topology
from .epic import AUTOMATION_ACTOR
from .sections import DEFAULT_PLAN_SECTIONS, Section, render_scaffold, unfilled_sections


def _edges_into(state: str, topology: Topology) -> list[str]:
    """Every ``edge:<from>--<state>`` key, from ANY state — forced transitions
    fire real (possibly non-topology) edge keys, so safety-relevant
    subscriptions enumerate the full product, not just legal edges."""
    return [f"edge:{src}--{state}" for src in topology.states if src != state or state == "execute"]


class PlanScaffoldExtension:
    """Writes the plan.md scaffold on entering ``plan`` (in-lock).

    Idempotent: an existing plan.md is preserved and noted in the work_log
    (supervisor decision, epic HATS-1014). Writes the file directly —
    fs-as-truth, no doc-store API (K2 owns that surface).
    """

    name = "plan-scaffold"

    def __init__(
        self,
        tasks_dir: Path,
        sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS,
        *,
        topology: Topology | None = None,
        priority: int = 30,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.sections = sections
        self._topology = topology if topology is not None else load_topology()
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in _edges_into("plan", self._topology)
        ]

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
    ``done → execute`` is not gated — the plan already passed once (HATS-328).
    """

    name = "plan-gate"

    def __init__(
        self,
        tasks_dir: Path,
        sections: tuple[Section, ...] = DEFAULT_PLAN_SECTIONS,
        *,
        topology: Topology | None = None,
        priority: int = 10,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.sections = sections
        self._topology = topology if topology is not None else load_topology()
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in _edges_into("execute", self._topology)
            if key != "edge:done--execute"  # reopen is not gated (HATS-328)
        ]

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
