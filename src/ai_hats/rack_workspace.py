"""Integrator-side rack workspace facade for the HYP/PROP consumers (HATS-1044 R6).

The reflect / judge / quorum-autoclose / session-review consumers reach the
HYP and PROP backlogs through the rack :class:`Workspace` here instead of the
retired ``ai_hats_tracker`` stores. Reads return small views (the fields those
consumers render); writes go through the field-owning extensions
(``hyp-verdicts``/``prop-votes``) and named FSM edges. Card CREATE is a direct
dir-per-card write under the catalog alloc-lock — the kernel's ``create`` cannot
allocate a card whose required declared fields (``hypothesis``/``category`` …) it
does not accept, so this mirrors the migration writer instead.

Import-hygiene: this is the integrator boundary; the rack imports no first-party
code, and this module imports no ``ai_hats_tracker``.
"""  # comment-length: allow

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from filelock import FileLock

from ai_hats_core import atomic_write_text
from ai_hats_rack import Workspace
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import UnknownExtensionError, UnknownPrefixError

from .paths import tasks_dir

#: Actor stamped on integrator-side rack writes (reflect/judge provenance).
REFLECT_ACTOR = "rack:reflect"


def rack_workspace(project_dir: Path) -> Workspace:
    """Discover the workspace for a project: the tasks catalog plus the sibling
    HYP/PROP catalogs under ``<ai_hats_dir>/tracker`` (mounted once migrated)."""
    root = RackRoot(project_dir=project_dir, tasks_dir=tasks_dir(project_dir))
    return Workspace.discover([root])


# ----- read views -------------------------------------------------------------


@dataclass(frozen=True)
class HypView:
    """The HYP fields the reflect/judge/session-review consumers render.
    ``status`` is the rack ``state`` (active|confirmed|refuted|stalled)."""

    id: str
    title: str
    status: str
    hypothesis: str
    success_criterion: str | None
    observation_window: str | None
    verification_protocol: str | None
    validation_log: tuple[dict, ...]


@dataclass(frozen=True)
class PropView:
    """The PROP fields the consumers render. ``status`` is the rack ``state``."""

    id: str
    title: str
    status: str
    category: str
    target: str
    description: str
    rationale: str
    votes: tuple[dict, ...]
    related_hypotheses: tuple[str, ...]
    failed_session_id: str | None


def _hyp_view(card) -> HypView:
    e = card.extras
    return HypView(
        id=card.id,
        title=card.title,
        status=card.state,
        hypothesis=str(e.get("hypothesis") or ""),
        success_criterion=e.get("success_criterion") or None,
        observation_window=e.get("observation_window") or None,
        verification_protocol=e.get("verification_protocol") or None,
        validation_log=tuple(e.get("validation_log") or ()),
    )


def _prop_view(card) -> PropView:
    e = card.extras
    return PropView(
        id=card.id,
        title=card.title,
        status=card.state,
        category=str(e.get("category") or ""),
        target=str(e.get("target") or ""),
        description=str(e.get("description") or ""),
        rationale=str(e.get("rationale") or ""),
        votes=tuple(e.get("votes") or ()),
        related_hypotheses=tuple(card.links.get("related_hypotheses") or ()),
        failed_session_id=(e.get("failed_session_id") or None),
    )


def _catalog(ws: Workspace, prefix_probe: str) -> Path | None:
    """The catalog dir of the backlog a probe id routes to, or ``None`` when that
    backlog is not mounted (pre-migration: no ``backlog.yaml`` → no read/write)."""
    try:
        return ws.instance_for(prefix_probe).catalog
    except UnknownPrefixError:
        return None


def _load_cards(catalog: Path | None):
    from ai_hats_rack.models import TaskCard

    if catalog is None or not catalog.is_dir():
        return []
    cards = []
    for path in sorted(catalog.glob("*/task.yaml"), key=lambda p: _id_key(p.parent.name)):
        try:
            cards.append(TaskCard.from_yaml(path))
        except Exception:  # noqa: BLE001, S112 — one corrupt card must not sink the listing
            continue
    return cards


def _id_key(name: str) -> tuple[str, int]:
    import re

    m = re.search(r"(\d+)$", name)
    return (name[: m.start()] if m else name, int(m.group(1)) if m else -1)


def active_hypotheses(ws: Workspace) -> list[HypView]:
    """Every ``active`` HYP as a view; empty when the HYP backlog is unmounted."""
    return [_hyp_view(c) for c in _load_cards(_catalog(ws, "HYP-0")) if c.state == "active"]


def active_hypothesis_ids(ws: Workspace) -> set[str]:
    return {h.id for h in active_hypotheses(ws)}


def proposals(
    ws: Workspace, *, status: str | None = None, category: str | None = None, target: str | None = None
) -> list[PropView]:
    """PROP views filtered by state/category/target (AND-combined)."""
    out = [_prop_view(c) for c in _load_cards(_catalog(ws, "PROP-0"))]
    if status is not None:
        out = [p for p in out if p.status == status]
    if category is not None:
        out = [p for p in out if p.category == category]
    if target is not None:
        out = [p for p in out if p.target == target]
    return out


def open_proposals(ws: Workspace) -> list[PropView]:
    return proposals(ws, status="open")


# ----- writes -----------------------------------------------------------------


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_id(catalog: Path, prefix: str) -> str:
    """Next ``<prefix>-NNN`` across BOTH the flat sources and the dir-per-card
    cards (they coexist post-migration) — the alloc lock aligns with the kernel."""
    import re

    max_n = 0
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)")
    if catalog.is_dir():
        for entry in catalog.iterdir():
            m = pat.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"{prefix}-{max_n + 1:03d}"


def _create_card(ws: Workspace, prefix: str, body: dict, links: dict[str, list[str]]) -> str:
    """Allocate the next id under the catalog alloc-lock and write a minimal
    dir-per-card ``task.yaml`` directly (the kernel's create cannot allocate a
    HYP/PROP whose required declared fields it does not accept)."""
    instance = ws.instance_for(f"{prefix}-0")
    catalog = instance.catalog
    catalog.mkdir(parents=True, exist_ok=True)
    with FileLock(str(catalog / ".alloc.lock")):
        new_id = _next_id(catalog, prefix)
        card = {"id": new_id, **body}
        clean_links = {k: v for k, v in links.items() if v}
        if clean_links:
            card["links"] = clean_links
        dest = catalog / new_id / "task.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dest, yaml.safe_dump(card, sort_keys=False, allow_unicode=True))
    return new_id


def create_hypothesis(
    ws: Workspace,
    *,
    title: str,
    hypothesis: str,
    source_task: str,
    baseline: str | None = None,
    expected_outcome=(),
    success_criterion: str | None = None,
    exit_criteria: dict | None = None,
) -> str:
    """Create a new active HYP (returns its id). ``source_task`` rides the
    ``source_task`` link (dangling ok — a sentinel like ``supervisor-observation``
    is a legal provenance value, never existence-checked)."""
    body: dict = {
        "title": title,
        "state": "active",
        "created": datetime.now(timezone.utc).date().isoformat(),
        "source_task": source_task,
        "hypothesis": hypothesis,
    }
    if baseline is not None:
        body["baseline"] = baseline
    if expected_outcome:
        body["expected_outcome"] = list(expected_outcome)
    if success_criterion is not None:
        body["success_criterion"] = success_criterion
    if exit_criteria is not None:
        body["exit_criteria"] = exit_criteria
    # source_task is a rack link kind; keep it under links (matches the migration).
    source = body.pop("source_task")
    return _create_card(ws, "HYP", body, {"source_task": [source] if source else []})


def create_proposal(
    ws: Workspace,
    *,
    title: str,
    category: str,
    target: str,
    description: str,
    rationale: str,
    related_hypotheses=(),
    failed_session_id: str | None = None,
) -> str:
    """Create a new open PROP (returns its id)."""
    body: dict = {
        "title": title,
        "state": "open",
        "created": _utc_stamp(),
        "category": category,
        "target": target,
        "description": description,
        "rationale": rationale,
    }
    if failed_session_id:
        body["failed_session_id"] = failed_session_id
    return _create_card(ws, "PROP", body, {"related_hypotheses": list(related_hypotheses)})


def append_verdict(ws: Workspace, hyp_id: str, entry: dict, *, caller_cwd: Path, actor: str = REFLECT_ACTOR):
    """Append one validation_log entry to a HYP (io.append_verdict parity)."""
    return ws.extension("hyp-verdicts").append_verdict(hyp_id, entry, actor=actor, caller_cwd=caller_cwd)


def set_proposal_status(
    ws: Workspace, prop_id: str, to_state: str, *, caller_cwd: Path, actor: str = REFLECT_ACTOR
):
    """Transition a PROP along its named edge (accept/reject/defer/mark-duplicate);
    a card already in ``to_state`` is a no-op (idempotent re-triage)."""
    kernel = ws.kernel_for(prop_id)
    card = kernel.get(prop_id)
    if card is not None and card.state == to_state:
        return None
    return kernel.transition(prop_id, to_state, actor=actor, caller_cwd=caller_cwd, reason="reflect triage")


def autoclose_hypotheses(ws: Workspace, *, caller_cwd: Path, k: int, actor: str, dry_run: bool = False):
    """Run the quorum autoclose sweep; returns the closed :class:`QuorumClosure`s."""
    return ws.extension("hyp-verdicts").autoclose(caller_cwd=caller_cwd, k=k, actor=actor, dry_run=dry_run)


def hyp_backlog_mounted(ws: Workspace) -> bool:
    """Whether a HYP backlog is mounted (a migrated catalog with ``backlog.yaml``)."""
    try:
        ws.extension("hyp-verdicts")
        return True
    except (UnknownExtensionError, UnknownPrefixError):
        return False


__all__ = [
    "HypView",
    "PropView",
    "REFLECT_ACTOR",
    "active_hypotheses",
    "active_hypothesis_ids",
    "append_verdict",
    "autoclose_hypotheses",
    "create_hypothesis",
    "create_proposal",
    "hyp_backlog_mounted",
    "open_proposals",
    "proposals",
    "rack_workspace",
    "set_proposal_status",
]
