"""Integrator-side rack backlog facade for the retro/reflect consumers (HATS-1044 R6).

The reflect / judge / quorum-autoclose / session-review consumers reach the
HYP and PROP backlogs through the rack :class:`Workspace` here instead of the
retired ``ai_hats_tracker`` stores; the retro session window reads closed task
cards through :func:`closed_tasks` (HATS-1259). Reads return small views (the
fields those consumers render); writes go through the field-owning extensions
(``hyp-verdicts``/``prop-votes``) and named FSM edges. Card CREATE goes through
``kernel.create`` like every other write (HATS-1596): its ``fields``/``links``
mappings carry a custom backlog's declared fields and link kinds, so this road
needs no writer of its own.

Import-hygiene: this is the integrator boundary; the rack imports no first-party
code, and this module imports no ``ai_hats_tracker``.
"""  # comment-length: allow

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence, TypeVar


from ai_hats_core import atomic_write_text
from ai_hats_rack import Workspace
from ai_hats_rack.definition import packaged_definition_source
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.workspace import UnknownExtensionError, UnknownPrefixError

from .paths import ensure_ai_hats_dir, tasks_dir

#: Actor stamped on integrator-side rack writes (reflect/judge provenance).
REFLECT_ACTOR = "rack:reflect"

#: Actor stamped on the automatic post-session verdict harvest —
#: distinct from REFLECT_ACTOR (interactive judge) so a validation_log entry
#: shows which mechanism wrote it.
SESSION_REVIEWER_ACTOR = "rack:session-reviewer"


def rack_workspace(project_dir: Path) -> Workspace:
    """Discover the workspace for a project: the tasks catalog plus the sibling
    HYP/PROP catalogs under ``<ai_hats_dir>/tracker`` (mounted once migrated).

    Carries the check executor (HATS-1575). This road is not read-only —
    :func:`set_proposal_status` walks a PROP along a named FSM edge — so a
    workspace mounted without it runs every bound gate of every backlog as a
    silent no-op, the tasks catalog included.
    """  # comment-length: allow — that this road transitions at all is the point
    from .rack_consumers import check_port_factory

    # Anchor and owner coincide by construction here: the caller named the
    # project, and the backlog is that project's own.
    root = RackRoot(
        project_dir=project_dir, tasks_dir=tasks_dir(project_dir), backlog_owner=project_dir
    )
    return Workspace.discover([root], check_port=check_port_factory(project_dir))


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


class HasCreated(Protocol):
    created: str


V = TypeVar("V", bound=HasCreated)


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
    """Return views created at or before ``cut``.

    A date-only stamp (YYYY-MM-DD) is treated as start-of-day 00:00:00 UTC, so a card
    created on the session day remains included. An absent or unparseable stamp is kept (fail-open).
    """
    kept: list[V] = []
    for view in views:
        raw = view.created or ""
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


def _create_card(
    ws: Workspace, prefix: str, *, title: str, fields: dict, links: dict[str, list[str]]
) -> str:
    """Create through the kernel of the backlog ``prefix`` routes to (HATS-1596).

    One write path, so a HYP/PROP card gets what every other card gets: an alloc
    lock that times out instead of waiting forever, the write-strict schema, the
    initial state from the topology, link validation, and a journal entry.
    ``caller_cwd`` is the process cwd — the anchor the CLI route into this same
    kernel already passes.
    """  # comment-length: allow — what delegation buys back is the point
    instance = ws.instance_for(f"{prefix}-0")
    result = ws.kernel_for_instance(instance).create(
        actor=REFLECT_ACTOR,
        caller_cwd=Path.cwd(),
        title=title,
        fields=fields,
        links={kind: list(targets) for kind, targets in links.items() if targets},
    )
    return result.task.id


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
    """Create a new HYP (returns its id). A source_task that names an EXISTING
    card rides the ``source_task`` link; anything else — a sentinel like
    ``supervisor-observation``, or an id that no longer resolves — lands in the
    ``origin`` field rather than becoming an edge the kernel would refuse."""
    fields: dict = {"hypothesis": hypothesis}
    if baseline is not None:
        fields["baseline"] = baseline
    if expected_outcome:
        fields["expected_outcome"] = list(expected_outcome)
    if success_criterion is not None:
        fields["success_criterion"] = success_criterion
    if exit_criteria is not None:
        fields["exit_criteria"] = exit_criteria

    links: dict[str, list[str]] = {}
    if source_task:
        if ws.exists(source_task):
            links["source_task"] = [source_task]
        elif not origin:
            origin = source_task

    if origin:
        fields["origin"] = origin

    return _create_card(ws, "HYP", title=title, fields=fields, links=links)


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
    """Create a new PROP (returns its id)."""
    fields: dict = {
        "category": category,
        "target": target,
        "description": description,
        "rationale": rationale,
    }
    if failed_session_id:
        fields["failed_session_id"] = failed_session_id
    return _create_card(
        ws,
        "PROP",
        title=title,
        fields=fields,
        links={"related_hypotheses": list(related_hypotheses)},
    )


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
