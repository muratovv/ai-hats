"""``rack`` read surface v2: link / unlink / context / ls (HATS-1024, HATS-1029).

``context`` is THE one-call discovery package — card + document paths + one
top-level ``links`` object (HATS-1028) — replacing the 10-call, 209 851-char
baseline F4 walk. ``--with <pattern>`` embeds matching document content (capped
by ``--max-bytes``); ``--attr`` surfaces attribute feeds (audit, work_log).
``ls`` is either the backlog scan (no id) or a ticket-neighbourhood graph walk
(``ls <ID> --deep N [--link <pattern>]``) — the ``tree`` verb folded into it.
"""

from __future__ import annotations

from pathlib import Path

import click

from . import linked
from .audit_view import journal_view, record_lines
from .cli_common import JSON_OPT, TASKS_DIR_OPT, emit_json, fail, resolved_root

# shared table/mtime style — doc rows look the same on every rack surface
from .cli_doc import _columns, _mtime_human, echo_documents
from .kernel import LockTimeoutError, UnknownTaskError
from .linked import (
    DEFAULT_MAX_BYTES,
    ContextPackage,
    LinkView,
    Neighbor,
    SelfLinkError,
    build_context,
    card_filter,
    scan_cards,
    walk_neighborhood,
)
from .registry import DerivedLinkKindError, UnknownLinkKindError, load_registry_for
from .resolver import NoProjectRootError

#: the attribute feeds ``context --attr`` understands (the set the card left open).
KNOWN_ATTRS = ("audit", "work_log")
#: direction glyphs for a walk row: out → target, in ← target, both symmetric.
_ARROW = {"out": "→", "in": "←", "both": "↔"}


def handle_linked_error(exc: Exception, as_json: bool) -> None:
    """Typed, actionable failures (mirror of cli._handle_kernel_error)."""
    if isinstance(exc, UnknownTaskError):
        fail(as_json, "unknown_task", str(exc), task_id=exc.task_id)
    if isinstance(exc, SelfLinkError):
        fail(as_json, "self_link", str(exc), task_id=exc.task_id)
    if isinstance(exc, UnknownLinkKindError):
        fail(as_json, "unknown_link_kind", str(exc), kind=exc.kind, configured=list(exc.configured))
    if isinstance(exc, DerivedLinkKindError):
        fail(as_json, "derived_link_kind", str(exc), kind=exc.kind, inverse=exc.inverse)
    if isinstance(exc, NoProjectRootError):
        fail(as_json, "no_project_root", str(exc))
    if isinstance(exc, LockTimeoutError):
        fail(as_json, "lock_timeout", str(exc))
    raise exc


# link / unlink were absorbed into `rack transition --link/--unlink` (HATS-1030);
# this module keeps only the read surface (context / ls, tree folded into ls).


# ----- context -------------------------------------------------------------------


def _kind_label(kind: str) -> str:
    """Human header for a kind: ``depends_on`` → ``Depends on``."""
    return kind.replace("_", " ").capitalize()


def _echo_links(links: dict[str, tuple[LinkView, ...]]) -> None:
    if not links:
        return
    click.echo("")
    click.echo("  links:")
    for kind, views in links.items():
        click.echo(f"    {_kind_label(kind)}:")
        for view in views:
            res = f" — resolution: {view.resolution}" if view.resolution else ""
            click.echo(f"      {view.id} [{view.state}] {view.title}{res}")
            if view.docs:
                rows = [[ref.name, str(ref.path), _mtime_human(ref.mtime)] for ref in view.docs]
                for line in _columns(rows, "        "):
                    click.echo(line)


def _echo_context(pkg: ContextPackage, tasks_dir: Path) -> None:
    card = pkg.task
    for key in ("id", "title", "state", "priority"):
        value = getattr(card, key)
        if value:
            click.echo(f"  {key}: {value}")
    if card.tags:
        click.echo(f"  tags: {', '.join(card.tags)}")
    if card.resolution:
        click.echo(f"  resolution: {card.resolution}")
    if card.updated:
        click.echo(f"  updated: {card.updated}")
    if card.description:
        click.echo("  description:")
        for line in card.description.rstrip().splitlines():
            click.echo(f"    {line}")
    if card.work_log:
        latest = card.work_log[-1]
        click.echo(f"  latest work_log ({latest.timestamp or '?'}): {latest.message}")
    click.echo("")
    echo_documents(tasks_dir / card.id, list(pkg.documents))
    _echo_links(dict(pkg.links))
    for inc in pkg.included:
        click.echo("")
        click.echo(f"  --- {inc.task_id}/{inc.name} ({inc.path}) ---")
        click.echo(inc.content.rstrip("\n"))
        if inc.truncated:
            click.echo(f"  [truncated — {inc.size} bytes on disk; Read the full file: {inc.path}]")


def _tip() -> None:
    click.echo("")
    click.echo(
        "tip: paths only by design — Read them directly; embed doc content with "
        "--with '<glob>' (e.g. plan*|summary*), inspect attributes with "
        "--attr audit|work_log"
    )


def _collect_attrs(
    tasks_dir: Path,
    pkg: ContextPackage,
    attrs: tuple[str, ...],
    *,
    event: str | None,
    since: str | None,
    actor_filter: str | None,
) -> dict[str, object]:
    """Resolve the selected attribute feeds off the package's card."""
    out: dict[str, object] = {}
    if "audit" in attrs:
        view = journal_view(
            tasks_dir, pkg.task.id, pkg.task.state, event=event, since=since, actor=actor_filter
        )
        out["audit"] = view.to_dict()
    if "work_log" in attrs:
        out["work_log"] = [e.to_dict() for e in pkg.task.work_log]
    return out


def _echo_attrs(attrs: tuple[str, ...], payload: dict[str, object]) -> None:
    if "audit" in attrs:
        audit = payload["audit"]  # type: ignore[assignment]
        click.echo("")
        click.echo("  audit:")
        if not audit["records"]:
            click.echo("    (no journal records)")
        for record in audit["records"]:
            for line in record_lines(record):
                click.echo(f"    {line}")
        for warning in audit.get("warnings", []):
            click.echo(f"    warning: {warning}")
    if "work_log" in attrs:
        entries = payload["work_log"]  # type: ignore[assignment]
        click.echo("")
        click.echo("  work_log:")
        if not entries:
            click.echo("    (empty)")
        for entry in entries:
            click.echo(f"    {entry['timestamp'] or '?'} {entry['message']}")


@click.command("context")
@click.argument("task_id")
@click.option(
    "--with",
    "with_pattern",
    default=None,
    help="Embed docs whose name matches this glob (fnmatch + | alternation), e.g. plan*|summary*.",
)
@click.option(
    "--max-bytes",
    default=DEFAULT_MAX_BYTES,
    show_default=True,
    help="Per-document ceiling for --with content; a cut is marked with the path.",
)
@click.option(
    "--attr",
    "attrs",
    multiple=True,
    help="Attribute feed to include: audit, work_log (repeatable, comma-separable).",
)
@click.option("--event", "event_key", default=None, help="--attr audit only: exact event key.")
@click.option("--since", default=None, help="--attr audit only: ISO-8601 UTC lower bound (incl.).")
@click.option("--actor", "actor_filter", default=None, help="--attr audit only: exact actor.")
@TASKS_DIR_OPT
@JSON_OPT
def context_cmd(
    task_id: str,
    with_pattern: str | None,
    max_bytes: int,
    attrs: tuple[str, ...],
    event_key: str | None,
    since: str | None,
    actor_filter: str | None,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """One-call discovery package: card, document paths, and a top-level links object."""
    selected = tuple(
        dict.fromkeys(a.strip() for part in attrs for a in part.split(",") if a.strip())
    )
    unknown = [a for a in selected if a not in KNOWN_ATTRS]
    if unknown:
        fail(
            as_json,
            "unknown_attr",
            f"Unknown --attr {unknown[0]!r}: choose from {', '.join(KNOWN_ATTRS)}",
            attr=unknown[0],
            known=list(KNOWN_ATTRS),
        )
        return
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        registry = load_registry_for(root.project_dir)
        pkg = build_context(
            root.tasks_dir,
            task_id,
            registry=registry,
            with_pattern=with_pattern,
            max_bytes=max_bytes,
        )
        attr_payload = _collect_attrs(
            root.tasks_dir, pkg, selected, event=event_key, since=since, actor_filter=actor_filter
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    if as_json:
        out = pkg.to_dict()
        if attr_payload:
            out["attrs"] = attr_payload
        emit_json(out)
    else:
        _echo_context(pkg, root.tasks_dir)
        _echo_attrs(selected, attr_payload)
        _tip()


# ----- ls: backlog scan (no id) or neighbourhood walk (with id) -------------------


def _emit_scan(rows: list[linked.CardRow], as_json: bool) -> None:
    if as_json:
        emit_json({"tasks": [r.to_dict() for r in rows], "count": len(rows)})
        return
    if not rows:
        click.echo("No tasks match.")
        return
    table = [[r.id, f"[{r.state}]", r.priority, r.title] for r in rows]
    for line in _columns(table):
        click.echo(line)
    click.echo(f"  {len(rows)} task(s)  tip: rack context <ID> for the full package")


def _emit_walk(root_id: str, depth: int, neighbors: list[Neighbor], as_json: bool) -> None:
    if as_json:
        emit_json(
            {
                "root": root_id,
                "depth": depth,
                "neighbors": [n.to_dict() for n in neighbors],
                "count": len(neighbors),
            }
        )
        return
    if not neighbors:
        click.echo(f"{root_id}: no linked tasks within depth {depth}.")
        return
    click.echo(f"{root_id} — neighbourhood (depth {depth}):")
    rows = []
    for n in neighbors:
        arrow = _ARROW.get(n.direction, n.direction)
        chain = " › ".join(n.path) if depth > 1 else ""
        rows.append([n.id, f"[{n.state}/{n.priority}]", f"{arrow} {n.kind}", f"d{n.depth}", n.title, chain])
    for line in _columns(rows, "  "):
        click.echo(line)
    click.echo(f"  {len(neighbors)} linked task(s)")


@click.command("ls")
@click.argument("task_id", required=False)
@click.option(
    "--deep",
    default=None,
    type=int,
    help="With an ID: walk N link-edges out from it (BFS, default 1); tree folded in here.",
)
@click.option(
    "--link",
    "link_pattern",
    default=None,
    help="With an ID: only follow edge kinds matching this glob (fnmatch + | alternation).",
)
@click.option("--grep", default=None, help="Case-insensitive substring over title+description.")
@click.option("--tag", default=None, help="Exact tag match.")
@click.option("--state", default=None, help="Exact state match.")
@click.option("--parent", default=None, help="Filter to cards whose parent_task is this id.")
@TASKS_DIR_OPT
@JSON_OPT
def ls_cmd(
    task_id: str | None,
    deep: int | None,
    link_pattern: str | None,
    grep: str | None,
    tag: str | None,
    state: str | None,
    parent: str | None,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Backlog search (no ID) or a ticket-neighbourhood graph walk (rack ls <ID> --deep N)."""
    if task_id is None and (deep is not None or link_pattern is not None):
        fail(as_json, "invalid_request", "--deep/--link require a task id: rack ls <ID> --deep N")
        return
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        if task_id is None:
            rows = scan_cards(root.tasks_dir, grep=grep, tag=tag, state=state, parent=parent)
            _emit_scan(rows, as_json)
            return
        registry = load_registry_for(root.project_dir)
        neighbors = walk_neighborhood(
            root.tasks_dir,
            task_id,
            registry=registry,
            depth=deep if deep is not None else 1,
            link_pattern=link_pattern,
            row_filter=card_filter(grep=grep, tag=tag, state=state, parent=parent),
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    _emit_walk(task_id, deep if deep is not None else 1, neighbors, as_json)
