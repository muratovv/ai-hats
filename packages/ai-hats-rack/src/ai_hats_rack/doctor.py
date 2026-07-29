"""Backlog integrity report (HATS-1335): read-only diagnosis of already-written
data.

The strict-write guards (HATS-1327/1333) closed the SOURCE of broken links —
doctor inspects the STOCK: data landed by migration, hand-edited yaml, or
pre-guard writes. Never writes anything; repair stays a human decision
(``transition --link/--unlink``), because a "fixed" id is a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .linked import TargetChecker, _id_key, _kind_ids_readonly, card_exists
from .models import TaskCard
from .registry import LinksRegistry


@dataclass(frozen=True)
class Finding:
    """One integrity defect. ``kind`` names the link kind or card field the
    defect sits on; ``target`` the offending referenced id (both optional)."""

    check: str
    task_id: str
    detail: str
    kind: str = ""
    target: str = ""
    backlog: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = {"check": self.check, "task_id": self.task_id, "detail": self.detail}
        if self.kind:
            row["kind"] = self.kind
        if self.target:
            row["target"] = self.target
        if self.backlog:
            row["backlog"] = self.backlog
        return row


def _load_pass(tasks_dir: Path) -> tuple[dict[str, TaskCard], list[Finding]]:
    """Load every card directory; a directory that cannot produce a card is a
    FINDING — unlike ``scan_cards``, which silently skips it (linked.py)."""
    cards: dict[str, TaskCard] = {}
    findings: list[Finding] = []
    for card_dir in sorted(tasks_dir.iterdir(), key=lambda p: _id_key(p.name)):
        if not card_dir.is_dir() or card_dir.name.startswith("."):
            continue
        path = card_dir / "task.yaml"
        if not path.exists():
            findings.append(
                Finding("unreadable-card", card_dir.name, "card directory has no task.yaml")
            )
            continue
        try:
            cards[card_dir.name] = TaskCard.from_yaml(path)
        except Exception as exc:  # noqa: BLE001 — every load failure is one finding
            findings.append(
                Finding("unreadable-card", card_dir.name, f"task.yaml failed to load: {exc!r}")
            )
    return cards, findings


def _check_dangling(
    tasks_dir: Path,
    registry: LinksRegistry,
    cards: dict[str, TaskCard],
    exists: TargetChecker | None,
) -> list[Finding]:
    """Every stored edge must point at an existing card. Existence is on-disk
    presence (``card_exists``), not loadability — an unreadable target is
    already its own finding. Empty ids never reach the check (HATS-1333: an
    empty ``parent_task`` means "no parent"). Cross-backlog kinds
    (``kind.targets``) need the injected checker; without one they are skipped —
    the doctor verb always injects the workspace checker."""
    findings: list[Finding] = []
    for card in cards.values():
        for kind in registry.stored_kinds():
            for target in _kind_ids_readonly(kind, card):
                if not target:
                    continue
                if kind.targets:
                    if exists is None:
                        continue
                    found = exists(target, kind.targets)
                else:
                    found = card_exists(tasks_dir, target)
                if not found:
                    findings.append(
                        Finding(
                            "dangling-link",
                            card.id,
                            f"{kind.name} -> {target}: target not found",
                            kind=kind.name,
                            target=target,
                        )
                    )
    return findings


def diagnose_catalog(
    tasks_dir: Path,
    registry: LinksRegistry,
    *,
    exists: TargetChecker | None = None,
    backlog: str = "",
) -> list[Finding]:
    """Run every integrity check over one catalog; findings in scan order."""
    cards, findings = _load_pass(tasks_dir)
    findings += _check_dangling(tasks_dir, registry, cards, exists)
    if backlog:
        findings = [
            Finding(f.check, f.task_id, f.detail, f.kind, f.target, backlog) for f in findings
        ]
    return findings
