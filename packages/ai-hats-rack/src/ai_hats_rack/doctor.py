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
from .checks import DEAD, UNADDRESSED, BindingStatus, classify_bindings
from .fsm import Topology
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
    if not tasks_dir.is_dir():
        return cards, findings
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


def _check_mirror_drift(registry: LinksRegistry, cards: dict[str, TaskCard]) -> list[Finding]:
    """Every stored-inverse edge must have its back-edge: the ``mirror-link``
    reaction keeps the pair convergent at write time (definition.py refuses the
    kind without it), so a one-sided pair on disk is drift. Derived inverses
    (children) are computed, symmetric kinds self-pair, and a cross-backlog
    pair is the sibling catalog's mirror to keep — all skipped. A dangling or
    unreadable target is already its own finding, not drift too."""
    findings: list[Finding] = []
    for kind in registry.stored_kinds():
        if kind.symmetric or kind.targets or not kind.inverse:
            continue
        inverse = registry.get(kind.inverse)
        if inverse is None or inverse.derived:
            continue
        for card in cards.values():
            for target in _kind_ids_readonly(kind, card):
                other = cards.get(target)
                if other is None:
                    continue
                if card.id not in _kind_ids_readonly(inverse, other):
                    findings.append(
                        Finding(
                            "mirror-drift",
                            card.id,
                            f"{kind.name} -> {target} has no {inverse.name} back-edge",
                            kind=kind.name,
                            target=target,
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
    findings += _check_mirror_drift(registry, cards)
    findings += _check_duplicates(registry, cards)
    if schema is not None:
        findings += _check_required_fields(schema, cards)
    if backlog:
        findings = [
            Finding(f.check, f.task_id, f.detail, f.kind, f.target, backlog) for f in findings
        ]
    return findings


def diagnose_workspace(workspace) -> list[Finding]:
    """Diagnose every backlog mounted in the workspace, each labeled by its CLI
    name; cross-backlog refs resolve through the same existence checker the
    kernels use (``kind.targets`` -> sibling catalog)."""
    from .cardschema import build_card_schema
    from .composition import stock_validators

    findings: list[Finding] = []
    for instance in workspace.instances:
        defn = instance.definition
        findings += diagnose_catalog(
            instance.catalog,
            defn.links_registry,
            schema=build_card_schema(defn, stock_validators()),
            exists=workspace._existence_checker_for(instance),
            backlog=defn.cli_alias or instance.name,
        )
    return findings


# ----- the check channel (HATS-1584) -----------------------------------------

#: Which classified status is a finding. Every miss against the backlog the row
#: ADDRESSES is one (HATS-1774) — the skip HATS-1545 R10 made legal is a row that
#: arms under its OWN backlog, never an arrow borrowed from a sibling's grammar.
_BINDING_FINDINGS = {DEAD: "dead-check-point", UNADDRESSED: "unaddressable-check-row"}


@dataclass(frozen=True)
class BindingReport:
    """What the doctor can say about this project's declared gates.

    ``note`` is why the roster is empty when it is — a state that must never
    render as "clean", since a channel nobody can read is the silence the
    channel exists to remove.
    """

    rows: tuple[BindingStatus, ...] = ()
    findings: tuple[Finding, ...] = ()
    note: str = ""


def diagnose_bindings(workspace, *, owner: Path | None) -> BindingReport:
    """Classify every carried row against every topology mounted here.

    Asked ONCE, through the same port a transition runs: the rows come from the
    project that owns the backlog, so each mounted catalog answers with the same
    set and asking per catalog would re-compose the role N times over.

    ``owner`` is that project (``RackRoot.backlog_owner``). Without it the port
    answers "no rows" for a reason that is not "the role declares none", and
    reporting the two alike would call an unreadable channel clean.
    """
    factory = getattr(workspace, "check_port", None)
    if not callable(factory):
        return BindingReport(note=_NO_PORT)
    if owner is None:
        return BindingReport(note=_NO_OWNER)
    if not workspace.instances:
        return BindingReport(note="no backlog is mounted here, so no row can be judged")
    catalog = next(
        (i.catalog for i in workspace.instances if i.is_tasks), workspace.instances[0].catalog
    )
    try:
        reader = getattr(factory(catalog), "check_declarations", None)
        if not callable(reader):
            return BindingReport(note=_NO_READER)
        declarations = tuple(reader())
    except Exception as exc:  # noqa: BLE001 — every failure is a finding, never a traceback
        return BindingReport(findings=(Finding("unreadable-check-rows", "", str(exc)),))
    if not declarations:
        return BindingReport(note="no row is declared under apps.rack by the composed role")
    rows = classify_bindings(declarations, _mounted_topologies(workspace))
    return BindingReport(rows=rows, findings=tuple(_binding_findings(rows)))


def _mounted_topologies(workspace) -> dict[str, Topology]:
    """Every selector a mounted backlog answers to → its topology. Both spellings,
    because either addresses it (ADR-0017 §3) and keying on one would read an
    aliased row as unaddressed."""
    topologies: dict[str, Topology] = {}
    for instance in workspace.instances:
        for selector in (instance.name, instance.definition.cli_alias):
            if selector:
                topologies[selector] = instance.definition.topology
    return topologies


def _binding_findings(rows: tuple[BindingStatus, ...]) -> list[Finding]:
    """The unhappy statuses as findings, in the classifier's own words: what an
    ``edge:`` name means is the rack's question, so the recipe is written where
    the grammar lives."""
    return [
        Finding(check, "", row.detail, kind=row.selector)
        for row in rows
        if (check := _BINDING_FINDINGS.get(row.status))
    ]


_NO_PORT = (
    "no integrator supplies a check executor here, so no declared gate can be read — "
    "a bare rack has nothing to declare one with (ADR-0019 D11 clause 5)"
)

_NO_OWNER = (
    "no project owns this backlog, so no role composes onto it and nothing can declare a "
    "gate here — an empty roster below is that, not a role that declares none"
)

_NO_READER = (
    "the integrator supplying this project exposes no "
    "ai_hats_rack.checks.CheckPort.check_declarations — no declared gate can be read, "
    "so this report cannot tell an armed one from a dead one"
)
