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

from .cardschema import CardSchema
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


def _cycles(adj: dict[str, list[str]]) -> list[list[str]]:
    """Every cycle in a digraph, each as its node walk (no closing repeat).
    Iterative DFS — a pathological parent chain must not hit the recursion
    limit. A back-edge onto the gray path is a cycle; one DFS pass reports
    each distinct back-edge once."""
    white, gray = 0, 1
    color = dict.fromkeys(adj, white)
    cycles: list[list[str]] = []
    for root in adj:
        if color[root] != white:
            continue
        color[root] = gray
        stack = [(root, iter(adj[root]))]
        path = [root]
        while stack:
            node, edges = stack[-1]
            nxt = next(edges, None)
            if nxt is None:
                color[node] = 2
                stack.pop()
                path.pop()
                continue
            if color[nxt] == gray:
                cycles.append(path[path.index(nxt) :])
            elif color[nxt] == white:
                color[nxt] = gray
                stack.append((nxt, iter(adj[nxt])))
                path.append(nxt)
    return cycles


def _check_cycles(registry: LinksRegistry, cards: dict[str, TaskCard]) -> list[Finding]:
    """Transitive cycles on directional kinds — the shape the write-time pair
    guard deliberately leaves uncovered (HATS-1327: immediate A<->B only).
    Symmetric kinds are bidirectional by design; cross-backlog kinds cannot
    close a walk inside one catalog."""
    findings: list[Finding] = []
    for kind in registry.stored_kinds():
        if kind.symmetric or kind.targets:
            continue
        adj = {
            cid: [t for t in _kind_ids_readonly(kind, card) if t in cards]
            for cid, card in cards.items()
        }
        for nodes in _cycles(adj):
            start = min(range(len(nodes)), key=lambda i: _id_key(nodes[i]))
            walk = nodes[start:] + nodes[:start]
            findings.append(
                Finding(
                    "link-cycle",
                    walk[0],
                    " -> ".join([*walk, walk[0]]),
                    kind=kind.name,
                )
            )
    return findings


def _check_duplicates(registry: LinksRegistry, cards: dict[str, TaskCard]) -> list[Finding]:
    """A repeated id inside one list kind — the write path is idempotent, so a
    duplicate can only come from a raw edit; readers silently collapse it."""
    findings: list[Finding] = []
    for card in cards.values():
        for kind in registry.stored_kinds():
            if kind.arity != "many":
                continue
            ids = _kind_ids_readonly(kind, card)
            for dup in sorted({t for t in ids if ids.count(t) > 1}, key=_id_key):
                findings.append(
                    Finding(
                        "duplicate-link",
                        card.id,
                        f"{kind.name} lists {dup} {ids.count(dup)} times",
                        kind=kind.name,
                        target=dup,
                    )
                )
    return findings


def _check_required_fields(schema: CardSchema, cards: dict[str, TaskCard]) -> list[Finding]:
    """An unconditionally-required field must be non-empty on every card; a
    ``required_on`` field on every card SITTING in a gated state — the same
    contract the write path enforces on entry (cardschema state gates), here
    re-checked against data that predates the gate or bypassed it."""
    findings: list[Finding] = []
    for card in cards.values():
        data = card.to_dict()
        for f in schema.fields:
            if data.get(f.name):
                continue
            if f.required:
                findings.append(
                    Finding(
                        "missing-field",
                        card.id,
                        f"required field '{f.name}' is empty",
                        kind=f.name,
                    )
                )
            elif f.required_on and card.state in f.required_on:
                findings.append(
                    Finding(
                        "missing-field",
                        card.id,
                        f"'{f.name}' is required in state '{card.state}' but empty",
                        kind=f.name,
                    )
                )
    return findings


def diagnose_catalog(
    tasks_dir: Path,
    registry: LinksRegistry,
    *,
    schema: CardSchema | None = None,
    exists: TargetChecker | None = None,
    backlog: str = "",
) -> list[Finding]:
    """Run every integrity check over one catalog; findings in scan order."""
    cards, findings = _load_pass(tasks_dir)
    findings += _check_dangling(tasks_dir, registry, cards, exists)
    findings += _check_cycles(registry, cards)
    findings += _check_duplicates(registry, cards)
    if schema is not None:
        findings += _check_required_fields(schema, cards)
    if backlog:
        findings = [
            Finding(f.check, f.task_id, f.detail, f.kind, f.target, backlog) for f in findings
        ]
    return findings
