"""Derived views — STATE.md index regeneration as a post-lock extension.

Eventually consistent by design (epic HATS-1014 §2.2 rule 4): regeneration
runs after persist under the view's OWN lock and lands via atomic replace
(HATS-470 — no torn index on crash); lag is observable through the journal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..dispatch import Delta, DispatchContext, Phase, Subscription
from ..fsm import Topology, load_topology
from ..models import TaskCard, atomic_write_text


class DerivedViewsExtension:
    """Regenerates the STATE.md task index from the cards on disk."""

    name = "derived-views"

    def __init__(
        self,
        tasks_dir: Path,
        state_md_path: Path,
        *,
        topology: Topology | None = None,
        priority: int = 40,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.state_md_path = state_md_path
        self._topology = topology if topology is not None else load_topology()
        self._priority = priority

    def subscriptions(self) -> Sequence[Subscription]:
        states = self._topology.states
        keys = [
            f"edge:{src}--{dst}"
            for src in states
            for dst in states
            if src != dst or src == "execute"  # forced edges fire any pair; + reclaim loop
        ]
        keys.append("epicify")
        return [Subscription(key, Phase.POST_LOCK, self._priority) for key in keys]

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        self.refresh()
        return None

    def refresh(self) -> None:
        """Rescan the cards and atomically replace STATE.md under the view lock."""
        by_state: dict[str, list[TaskCard]] = {s: [] for s in self._topology.states}
        if self.tasks_dir.exists():
            for card_path in sorted(self.tasks_dir.glob("*/task.yaml")):
                try:
                    card = TaskCard.from_yaml(card_path)
                except (OSError, ValueError):
                    continue
                by_state.setdefault(card.state, []).append(card)

        lines = ["# Backlog state", ""]
        for state in by_state:
            cards = by_state[state]
            if not cards:
                continue
            lines.append(f"## {state.upper()}")
            lines.extend(f"- {c.id} [{c.priority}] {c.title}" for c in cards)
            lines.append("")

        self.state_md_path.parent.mkdir(parents=True, exist_ok=True)
        from filelock import FileLock

        lock = FileLock(str(self.state_md_path) + ".lock")
        with lock:
            atomic_write_text(self.state_md_path, "\n".join(lines) + "\n")
