"""Integrator-side rack backlog facade for the retro/reflect consumers (HATS-1044 R6).

The reflect / judge / quorum-autoclose / session-review consumers reach the
HYP and PROP backlogs through the rack :class:`Workspace` here instead of the
retired ``ai_hats_tracker`` stores; the retro session window reads closed task
cards through :func:`closed_tasks` (HATS-1259). Reads return small views (the
fields those consumers render); writes go through the field-owning extensions
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
from typing import Sequence, TypeVar

import yaml
from filelock import FileLock

from ai_hats_core import atomic_write_text
from ai_hats_rack import Workspace
from ai_hats_rack.definition import packaged_definition_source
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import UnknownExtensionError, UnknownPrefixError

from .paths import ensure_ai_hats_dir, tasks_dir

#: Actor stamped on integrator-side rack writes (reflect/judge provenance).
REFLECT_ACTOR = "rack:reflect"

#: Actor stamped on the automatic post-session verdict harvest (HATS-1369) —
#: distinct from REFLECT_ACTOR (interactive judge) so a validation_log entry
#: shows which mechanism wrote it.
SESSION_REVIEWER_ACTOR = "rack:session-reviewer"


def rack_workspace(project_dir: Path) -> Workspace:
    """Discover the workspace for a project: the tasks catalog plus the sibling
    HYP/PROP catalogs under ``<ai_hats_dir>/tracker`` (mounted once migrated)."""
    root = RackRoot(project_dir=project_dir, tasks_dir=tasks_dir(project_dir))
    return Workspace.discover([root])


def ensure_backlog(project_dir: Path, definition_name: str) -> None:
    """Seed a sibling backlog's ``backlog.yaml`` from the packaged definition when
    absent, so a write path (e.g. ``reflect issue``) can mount HYP/PROP on a
    project that never had one — parity with the pre-rack auto-create; idempotent.

    HATS-839 applies here and not on the rack path: the rack has its own validating
    resolver, but this facade is reached from ``cli/_helpers._project_dir``, which
    falls back to a bare cwd. Validate before the ``parents=True`` mkdir below, or a
    stray root gets a phantom tracker (HATS-1264).
    """
    catalog = tasks_dir(project_dir).parent / definition_name
    dest = catalog / "backlog.yaml"
    if not dest.is_file():
        ensure_ai_hats_dir(project_dir)
        catalog.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dest, packaged_definition_source(definition_name))


# ----- read views -------------------------------------------------------------


V = TypeVar("V")


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
    created: str = ""


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
    created: str = ""


@dataclass(frozen=True)
class ClosedTaskView:
    """A closed task card as the retro session window consumes it (HATS-1259).

    ``completed_at`` is the terminal-transition stamp, not ``updated`` — the
    latter is re-written by every card edit, so a long-closed task touched during
    a session would read as closed *in* it. Empty when the card predates the
    stamp; the caller decides how loudly to say so.
    """

    id: str
    completed_at: str


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
        created=str(card.created or ""),
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
        created=str(card.created or ""),
    )


def created_at_or_before(views: Sequence[V], cut: datetime) -> list[V]:
    """Вьюхи, заведённые не позже ``cut``.

    Штамп без времени (90 из 99 живых карточек) считается КОНЦОМ того дня, поэтому
    карточка, заведённая в день сессии, остаётся. Отсутствующий или неразбираемый
    штамп — fail-open: потерять живого кандидата хуже, чем пронести лишнего.
    """
    kept: list[V] = []
    for view in views:
        raw = getattr(view, "created", "") or ""
        dt = _created_dt(raw)
        if dt is None or dt <= cut:
            kept.append(view)
    return kept


def _created_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 10:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


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
        except Exception:  # silent-ok: one corrupt card must not sink the listing  # noqa: S112
            continue
    return cards


def _id_key(name: str) -> tuple[str, int]:
    import re

    m = re.search(r"(\d+)$", name)
    return (name[: m.start()] if m else name, int(m.group(1)) if m else -1)


def active_hypotheses(ws: Workspace) -> list[HypView]:
    """Every ``active`` HYP as a view; empty when the HYP backlog is unmounted."""
    return [_hyp_view(c) for c in _load_cards(_catalog(ws, "HYP-0")) if c.state == "active"]


def proposals(
    ws: Workspace,
    *,
    status: str | None = None,
    category: str | None = None,
    target: str | None = None,
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


#: The terminal state the retro counts as "closed work". ``cancelled`` is terminal
#: too and carries the same stamp, but is administrative closure, not work done.
_DONE = "done"


def closed_tasks(project_dir: Path) -> list[ClosedTaskView]:
    """Every task card in the terminal ``done`` state, id + close stamp.

    Takes the project rather than a :class:`Workspace`: the tasks catalog *is*
    the ``RackRoot.tasks_dir``, so routing by id prefix would only re-derive the
    directory ``tasks_dir`` already names.
    """
    return [
        ClosedTaskView(card.id, card.completed_at)
        for card in _load_cards(tasks_dir(project_dir))
        if card.state == _DONE
    ]


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


def _is_card_id(ws: Workspace, item_id: str) -> bool:
    if not item_id:
        return False
    try:
        ws.instance_for(item_id)
        return True
    except (UnknownExtensionError, UnknownPrefixError):
        return False


def create_hypothesis(
    ws: Workspace,
    *,
    title: str,
    hypothesis: str,
    source_task: str | None = None,
    origin: str | None = None,
    baseline: str | None = None,
    expected_outcome=(),
    success_criterion: str | None = None,
    exit_criteria: dict | None = None,
) -> str:
    """Create a new active HYP (returns its id). Real task IDs ride the
    ``source_task`` link; non-ID sentinels (like ``supervisor-observation``)
    land in the ``origin`` field."""
    body: dict = {
        "title": title,
        "state": "active",
        "created": datetime.now(timezone.utc).date().isoformat(),
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

    links: dict[str, list[str]] = {}
    if source_task:
        if _is_card_id(ws, source_task):
            links["source_task"] = [source_task]
        else:
            if not origin:
                origin = source_task

    if origin:
        body["origin"] = origin

    return _create_card(ws, "HYP", body, links)


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


def append_verdict(
    ws: Workspace, hyp_id: str, entry: dict, *, caller_cwd: Path, actor: str = REFLECT_ACTOR
):
    """Append one validation_log entry to a HYP (io.append_verdict parity)."""
    return ws.extension("hyp-verdicts").append_verdict(
        hyp_id, entry, actor=actor, caller_cwd=caller_cwd
    )


def set_proposal_status(
    ws: Workspace, prop_id: str, to_state: str, *, caller_cwd: Path, actor: str = REFLECT_ACTOR
):
    """Transition a PROP along its named edge (accept/reject/defer/mark-duplicate);
    a card already in ``to_state`` is a no-op (idempotent re-triage)."""
    kernel = ws.kernel_for(prop_id)
    card = kernel.get(prop_id)
    if card is not None and card.state == to_state:
        return None
    return kernel.transition(
        prop_id, to_state, actor=actor, caller_cwd=caller_cwd, reason="reflect triage"
    )


def autoclose_hypotheses(
    ws: Workspace, *, caller_cwd: Path, k: int, actor: str, dry_run: bool = False
):
    """Run the quorum autoclose sweep; returns the closed :class:`QuorumClosure`s."""
    return ws.extension("hyp-verdicts").autoclose(
        caller_cwd=caller_cwd, k=k, actor=actor, dry_run=dry_run
    )


def hyp_backlog_mounted(ws: Workspace) -> bool:
    """Whether a HYP backlog is mounted (a migrated catalog with ``backlog.yaml``)."""
    try:
        ws.extension("hyp-verdicts")
        return True
    except (UnknownExtensionError, UnknownPrefixError):
        return False


__all__ = [
    "ClosedTaskView",
    "HypView",
    "PropView",
    "REFLECT_ACTOR",
    "SESSION_REVIEWER_ACTOR",
    "active_hypotheses",
    "append_verdict",
    "autoclose_hypotheses",
    "closed_tasks",
    "create_hypothesis",
    "create_proposal",
    "created_at_or_before",
    "hyp_backlog_mounted",
    "open_proposals",
    "proposals",
    "rack_workspace",
    "set_proposal_status",
]
