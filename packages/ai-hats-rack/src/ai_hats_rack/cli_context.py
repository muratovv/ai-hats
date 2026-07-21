"""``rack`` read surface: context / ls (HATS-1024, HATS-1029, HATS-1031).

``context`` is THE one-call read surface — full card + document paths + one
top-level ``links`` object (HATS-1028) — replacing the 10-call, 209 851-char
baseline F4 walk and, since HATS-1031 (Р11), the killed ``show`` verb.
``--with <pattern>`` embeds matching document content (capped by
``--max-bytes``); ``--attr`` surfaces attribute feeds (audit, work_log).
``ls`` is either the backlog scan (no id) or a ticket-neighbourhood graph walk
(``ls <ID> --deep N [--link <pattern>]``) — the ``tree`` verb folded into it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from . import linked
from .audit_view import journal_view, record_lines
from .cli_common import (
    JSON_OPT,
    TASKS_DIR_OPT,
    emit_json,
    fail,
    handle_rack_error,
    resolve_error,
    resolve_roots,
    resolved_root,
)
from .composition import build_read_subscribers, stock_factories
from .docstore import DocInfo
from .linked import (
    DEFAULT_MAX_BYTES,
    ContextPackage,
    LinkView,
    Neighbor,
    build_context,
    card_filter,
    scan_cards,
    walk_neighborhood,
)
from .workspace import Workspace

#: the attribute feeds ``context --attr`` understands (the set the card left open).
KNOWN_ATTRS = ("audit", "work_log")
#: direction glyphs for a walk row: out → target, in ← target, both symmetric.
_ARROW = {"out": "→", "in": "←", "both": "↔"}
#: blunt backstop against an accidental large ``ls`` scan / walk flooding agent
#: context; ``--all`` removes it. Applies to human AND json (map-not-filter), not
#: the semantic open-only frontier filter (that is HATS-1048). See HATS-1047.
DEFAULT_LS_LIMIT = 30


# ----- shared table/mtime/documents rendering (ex-cli_doc, HATS-1021/1031) --------


def _mtime_human(mtime_iso: str) -> str:
    if not mtime_iso:
        return "—"
    ts = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00"))
    return ts.astimezone().strftime("%Y-%m-%d %H:%M")


def _frozen_mark(doc: DocInfo) -> str:
    if not doc.frozen:
        return ""
    return f"frozen ✗ {doc.drift}" if doc.drift else "frozen ✓"


def _columns(rows: list[list[str]], indent: str = "  ") -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [
        indent + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows
    ]


def echo_documents(card_dir: Path, docs: list[DocInfo], indent: str = "  ") -> None:
    """The documents block: name + ABSOLUTE path + mtime + frozen mark. Content
    is never inlined — the agent Reads by the printed path (discovery model)."""
    click.echo(f"{indent}Documents ({card_dir.absolute()}):")
    if not docs:
        click.echo(f"{indent}  (none — write files into this directory to add)")
        return
    rows = [[d.name, str(d.path), _mtime_human(d.mtime), _frozen_mark(d)] for d in docs]
    for line in _columns(rows, indent + "  "):
        click.echo(line)


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
    # `reviewer` rides the head since HATS-1031: show-parity, one read surface.
    for key in ("id", "title", "state", "priority", "reviewer"):
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
        click.echo("  work_log:")  # the ex-show tail; full feed: --attr work_log
        for entry in card.work_log[-5:]:
            click.echo(f"    {entry.timestamp} {entry.message}")
    click.echo("")
    echo_documents(tasks_dir / card.id, list(pkg.documents))
    _echo_links(dict(pkg.links))
    for contrib in pkg.enrichments:
        click.echo("")
        click.echo(f"  === {contrib.name} ===")
        for line in contrib.body.rstrip().splitlines():
            click.echo(f"  {line}")
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
        "repeatable --with '<glob>' (e.g. --with 'plan*' --with 'summary*'), "
        "inspect attributes with --attr audit|work_log"
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
@click.argument("task_ids", nargs=-1)
@click.option(
    "--with",
    "with_patterns",
    multiple=True,
    help="Embed docs whose name matches this glob; repeatable, OR-combined "
    "(e.g. --with 'plan*' --with 'summary*').",
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
    task_ids: tuple[str, ...],
    with_patterns: tuple[str, ...],
    max_bytes: int,
    attrs: tuple[str, ...],
    event_key: str | None,
    since: str | None,
    actor_filter: str | None,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """One-call discovery package for one or more task ids: card, document paths,
    and a top-level ``links`` object.

    Batch (``context ID1 ID2 …``) assembles every id in ONE process, amortizing
    the interpreter/import start-up tax and the ``Workspace`` walk across the
    viewport (HATS-1074) so a consumer can prefetch instead of spawning per id.
    One id → the legacy unwrapped payload, byte-identical. ``≥2`` ids → a
    ``{"contexts": {id: …}}`` map, skip-and-continue: a bad id yields a per-id
    ``error`` entry while the rest still resolve (single-id keeps fail-fast).
    """
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
    ids = tuple(dict.fromkeys(task_ids))  # dedup, order-preserving
    if not ids:
        fail(as_json, "invalid_request", "provide at least one task id")
        return

    # Resolve the workspace ONCE: the discover walk + stock factories are the
    # per-invocation setup a batch exists to amortize (HATS-1074). Read
    # subscribers are memoized per catalog (a viewport is usually one prefix).
    try:
        workspace = Workspace.discover([resolved_root(tasks_dir, Path.cwd())])
        factories = stock_factories()
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return
    subs_cache: dict[Path, list] = {}

    def _assemble(task_id: str) -> tuple[ContextPackage, dict[str, object], Path]:
        # Route by the id's prefix (HATS-1044): a tasks-only repo resolves to the
        # tasks catalog — same registry, same output as before.
        instance = workspace.instance_for(task_id)
        catalog = instance.catalog
        defn = instance.definition
        read_subscribers = subs_cache.get(catalog)
        if read_subscribers is None:
            read_subscribers = build_read_subscribers(defn, catalog, factories)
            subs_cache[catalog] = read_subscribers
        pkg = build_context(
            catalog,
            task_id,
            registry=defn.links_registry,
            with_patterns=with_patterns,
            max_bytes=max_bytes,
            read_subscribers=read_subscribers,
        )
        attr_payload = _collect_attrs(
            catalog, pkg, selected, event=event_key, since=since, actor_filter=actor_filter
        )
        return pkg, attr_payload, catalog

    def _json_entry(pkg: ContextPackage, attr_payload: dict[str, object]) -> dict[str, object]:
        entry = pkg.to_dict()
        if attr_payload:
            entry["attrs"] = attr_payload
        return entry

    if len(ids) == 1:
        try:
            pkg, attr_payload, catalog = _assemble(ids[0])
        except Exception as exc:  # noqa: BLE001 — routed to typed handling
            handle_rack_error(exc, as_json)
            return
        if as_json:
            emit_json(_json_entry(pkg, attr_payload))
        else:
            _echo_context(pkg, catalog)
            _echo_attrs(selected, attr_payload)
            _tip()
        return

    # Batch (≥2 ids): one process, skip-and-continue on a per-id failure. A truly
    # unknown exception (no typed handler) still re-raises, as single-id does.
    if as_json:
        contexts: dict[str, object] = {}
        for task_id in ids:
            try:
                pkg, attr_payload, _ = _assemble(task_id)
            except Exception as exc:  # noqa: BLE001 — per-id typed error; rest continue
                resolved = resolve_error(exc)
                if resolved is None:
                    raise
                code, message, details = resolved
                contexts[task_id] = {"error": {"code": code, "message": message, **details}}
                continue
            contexts[task_id] = _json_entry(pkg, attr_payload)
        emit_json({"contexts": contexts})
        return

    for index, task_id in enumerate(ids):
        if index:
            click.echo("")  # blank line separates cards; each card opens with `id:`
        try:
            pkg, attr_payload, catalog = _assemble(task_id)
        except Exception as exc:  # noqa: BLE001 — per-id typed error; rest continue
            resolved = resolve_error(exc)
            if resolved is None:
                raise
            click.echo(f"  {task_id}: error: {resolved[1]}")
            continue
        _echo_context(pkg, catalog)
        _echo_attrs(selected, attr_payload)
    _tip()


# ----- ls: backlog scan (no id) or neighbourhood walk (with id) -------------------


def _cap(items: list, show_all: bool) -> tuple[list, int, bool]:
    """(shown, total, capped) — blunt DEFAULT_LS_LIMIT backstop unless ``--all``."""
    total = len(items)
    capped = not show_all and total > DEFAULT_LS_LIMIT
    return (items[:DEFAULT_LS_LIMIT] if capped else items), total, capped


def _emit_scan(
    rows: list[linked.CardRow],
    as_json: bool,
    show_all: bool,
    *,
    show_backlog: bool = False,
    show_project: bool = False,
) -> None:
    shown, total, capped = _cap(rows, show_all)
    if as_json:
        emit_json(
            {
                "tasks": [r.to_dict() for r in shown],
                "count": len(shown),
                "total": total,
                "capped": capped,
            }
        )
        return
    if not rows:
        click.echo("No tasks match.")
        return

    def _cols(r: linked.CardRow) -> list[str]:
        # marker columns lead (project, then backlog) only when >1 in view (R7)
        lead = ([r.project] if show_project else []) + ([r.backlog] if show_backlog else [])
        return [*lead, r.id, f"[{r.state}]", r.priority, r.title]

    for line in _columns([_cols(r) for r in shown]):
        click.echo(line)
    if capped:
        click.echo(
            f"  showing {DEFAULT_LS_LIMIT} of {total} — --all for all, "
            "or narrow with --tag/--state/--parent"
        )
    elif show_project or show_backlog:
        spans = []
        if show_project:
            spans.append(f"{len({r.project for r in shown})} project(s)")
        if show_backlog:
            spans.append(f"{len({r.backlog for r in shown})} backlog(s)")
        click.echo(f"  {total} card(s) across {', '.join(spans)}")
    else:
        click.echo(f"  {total} task(s)  tip: rack context <ID> for the full package")


def _emit_walk(
    root_id: str, depth: int, neighbors: list[Neighbor], as_json: bool, show_all: bool
) -> None:
    shown, total, capped = _cap(neighbors, show_all)
    if as_json:
        emit_json(
            {
                "root": root_id,
                "depth": depth,
                "neighbors": [n.to_dict() for n in shown],
                "count": len(shown),
                "total": total,
                "capped": capped,
            }
        )
        return
    if not neighbors:
        click.echo(f"{root_id}: no linked tasks within depth {depth}.")
        return
    click.echo(f"{root_id} — neighbourhood (depth {depth}):")
    rows = []
    for n in shown:
        arrow = _ARROW.get(n.direction, n.direction)
        chain = " › ".join(n.path) if depth > 1 else ""
        rows.append(
            [n.id, f"[{n.state}/{n.priority}]", f"{arrow} {n.kind}", f"d{n.depth}", n.title, chain]
        )
    for line in _columns(rows, "  "):
        click.echo(line)
    if capped:
        click.echo(
            f"  showing {DEFAULT_LS_LIMIT} of {total} — --all for all, or narrow with --link/--state"
        )
    else:
        click.echo(f"  {total} linked task(s)")


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
    "link_patterns",
    multiple=True,
    help="With an ID: only follow edge kinds matching this glob; repeatable, "
    "OR-combined (e.g. --link 'parent_task' --link 'children').",
)
@click.option("--grep", default=None, help="Case-insensitive substring over title+description.")
@click.option("--tag", default=None, help="Exact tag match.")
@click.option("--state", default=None, help="Exact state match.")
@click.option("--parent", default=None, help="Filter to cards whose parent_task is this id.")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help=f"Remove the {DEFAULT_LS_LIMIT}-row output cap (default caps human & json alike).",
)
@click.option(
    "--backlog",
    multiple=True,
    help="No-id scan: scan a mounted backlog by name (cli_alias or name), e.g. "
    "--backlog hyp. Repeatable to name a subset (--backlog hyp --backlog proposal); "
    "default is the tasks catalog, --all-backlogs is every one. Mounted backlogs "
    "also list as groups in `rack --help`.",
)
@click.option(
    "--all-backlogs",
    "all_backlogs",
    is_flag=True,
    help="No-id scan: scan every mounted backlog (tasks + siblings) interleaved.",
)
@click.option(
    "--root",
    "root_flags",
    multiple=True,
    help="No-id scan: also scan this project root, added to the current one "
    "(repeatable, HATS-1081); rows are marked by project. Default is the current "
    "project only.",
)
@TASKS_DIR_OPT
@JSON_OPT
def ls_cmd(
    task_id: str | None,
    deep: int | None,
    link_patterns: tuple[str, ...],
    grep: str | None,
    tag: str | None,
    state: str | None,
    parent: str | None,
    show_all: bool,
    backlog: tuple[str, ...],
    all_backlogs: bool,
    root_flags: tuple[str, ...],
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Backlog search (no ID) or a ticket-neighbourhood graph walk (rack ls <ID> --deep N)."""
    if task_id is None and (deep is not None or link_patterns):
        fail(as_json, "invalid_request", "--deep/--link require a task id: rack ls <ID> --deep N")
        return
    if task_id is not None and (backlog or all_backlogs or root_flags):
        fail(
            as_json,
            "invalid_request",
            "--backlog/--all-backlogs/--root apply to the no-id scan only",
        )
        return
    if backlog and all_backlogs:
        fail(as_json, "invalid_request", "--backlog and --all-backlogs are mutually exclusive")
        return
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        if task_id is None:
            workspace = Workspace.discover(resolve_roots(tasks_dir, Path.cwd(), root_flags))
            if all_backlogs:
                selected = list(workspace.instances)
            elif backlog:
                seen: set[str] = set()
                selected = []
                for name in backlog:  # repeatable; dedup so a repeat is not double-scanned
                    inst = workspace.instance_by_name(name)
                    if inst.name not in seen:
                        seen.add(inst.name)
                        selected.append(inst)
            else:
                selected = [i for i in workspace.instances if i.is_tasks]
            # Stamp origin only when the matching filter is engaged, so the default
            # `rack ls` output stays annotation-free (R2, HATS-1080/1081).
            backlog_feature = bool(backlog or all_backlogs)
            project_feature = bool(root_flags)
            rows = [
                row
                for inst in selected
                for row in scan_cards(
                    inst.catalog,
                    grep=grep,
                    tag=tag,
                    state=state,
                    parent=parent,
                    backlog=(inst.definition.cli_alias or inst.name) if backlog_feature else "",
                    project=inst.root_id if project_feature else "",
                )
            ]
            _emit_scan(
                rows,
                as_json,
                show_all,
                show_backlog=len({i.name for i in selected}) > 1,
                show_project=len({i.root_id for i in selected}) > 1,
            )
            return
        instance = Workspace.discover([root]).instance_for(task_id)
        neighbors = walk_neighborhood(
            instance.catalog,
            task_id,
            registry=instance.definition.links_registry,
            depth=deep if deep is not None else 1,
            link_patterns=link_patterns,
            row_filter=card_filter(grep=grep, tag=tag, state=state, parent=parent),
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return
    _emit_walk(task_id, deep if deep is not None else 1, neighbors, as_json, show_all)
