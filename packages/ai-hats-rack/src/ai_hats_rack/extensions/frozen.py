"""Frozen-pin integrity guard (HATS-1031 Р13): drifted evidence blocks moves.

The doc-store view only MARKS drift; this stock extension ENFORCES it — an
in-lock subscriber on every edge that scans the task's frozen pins and aborts
the transition when any pinned document changed or vanished, with the recovery
recipe in the reason. Business logic above the core: the kernel just propagates
the abort (nothing persisted).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..dispatch import AbortOperation, Delta, DispatchContext, Phase, Subscription
from ..docstore import _card_pins, compute_digest
from ..fsm import Topology, load_topology


def _all_edge_keys(topology: Topology) -> list[str]:
    """The full ``edge:<from>--<to>`` product (+ the execute reclaim self-loop):
    forced transitions fire real non-topology edge keys, so safety-relevant
    subscriptions enumerate the product, not just legal edges (rack_wiring
    precedent)."""
    states = topology.states
    return [
        f"edge:{src}--{dst}" for src in states for dst in states if src != dst or src == "execute"
    ]


class FrozenIntegrityExtension:
    """Blocks EVERY transition of a task whose frozen pins drifted (in-lock).

    Pins are read from ``ctx.task`` — the in-memory card, so a composite's
    earlier ``--freeze``/``--rm`` op is already visible to a later state op —
    and the files are digested from disk. No waivers: force, epics and
    automation actors do not bypass evidence integrity (the HATS-518 rule:
    force relaxes the FSM arrow, never a safety contract).
    """

    name = "frozen-integrity"

    def __init__(
        self,
        tasks_dir: Path,
        *,
        topology: Topology | None = None,
        priority: int = 8,
    ) -> None:
        self.tasks_dir = tasks_dir
        self._topology = topology if topology is not None else load_topology()
        # 8 = after the session-slot guard (5), before the plan-gate (10):
        # data-integrity refusals outrank workflow gating (HATS-1031 plan §5).
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        return [
            Subscription(key, Phase.IN_LOCK, self._priority)
            for key in _all_edge_keys(self._topology)
        ]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        card_dir = self.tasks_dir / ctx.task.id
        drifted = []
        for pin in _card_pins(ctx.task):
            name = str(pin["name"])
            pinned = str(pin.get("digest", ""))
            path = card_dir / name
            current = compute_digest(path) if path.is_file() else ""
            if current != pinned:
                drifted.append(_recipe(ctx.task.id, name, pinned, current))
        if drifted:
            raise AbortOperation(
                "frozen evidence drifted — transition blocked: " + "; ".join(drifted)
            )
        return None


def _recipe(task_id: str, name: str, pinned: str, current: str) -> str:
    """One actionable drift line: document + digests + the transition-op hatch."""
    if not current:
        return (
            f"'{name}' pinned {pinned} but the file is missing — restore it, or drop "
            f"the pin: rack transition {task_id} --rm {name} --ack-frozen"
        )
    return (
        f"'{name}' pinned {pinned}, on disk {current} — accept the new content: "
        f"rack transition {task_id} --freeze {name} --ack-frozen; or drop it: "
        f"rack transition {task_id} --rm {name} --ack-frozen"
    )
