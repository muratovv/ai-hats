"""``rack`` linked-task verbs: tree / link / unlink / context / ls (HATS-1024).

``context`` is THE one-call discovery package — card + document paths + one
top-level ``links`` object (HATS-1028) — replacing the 10-call, 209 851-char
baseline F4 walk. Content rides only behind an explicit ``--with`` selector,
capped per document by ``--max-bytes`` with a marked, path-carrying cut.
"""

from __future__ import annotations

from pathlib import Path

import click

from .cli_common import JSON_OPT, TASKS_DIR_OPT, emit_json, fail, resolved_root

# shared table/mtime style — doc rows look the same on every rack surface
from .cli_doc import _columns, _mtime_human, echo_documents
from .docstore import UnknownDocumentError
from .kernel import LockTimeoutError, UnknownTaskError
from .linked import (
    DEFAULT_MAX_BYTES,
    ContextPackage,
    LinkView,
    SelfLinkError,
    TreeNode,
    UnknownSelectorError,
    build_context,
    build_tree,
    scan_cards,
)
from .registry import DerivedLinkKindError, UnknownLinkKindError, load_registry_for
from .resolver import NoProjectRootError


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
    if isinstance(exc, UnknownSelectorError):
        fail(as_json, "unknown_selector", str(exc), selector=exc.selector)
    if isinstance(exc, UnknownDocumentError):
        fail(as_json, "unknown_document", str(exc), task_id=exc.task_id, name=exc.name)
    if isinstance(exc, NoProjectRootError):
        fail(as_json, "no_project_root", str(exc))
    if isinstance(exc, LockTimeoutError):
        fail(as_json, "lock_timeout", str(exc))
    raise exc


# ----- tree --------------------------------------------------------------------


def _label(node: TreeNode) -> str:
    return f"{node.id} [{node.state}/{node.priority}] {node.title}".rstrip()


def _tree_lines(root: TreeNode) -> list[str]:
    lines = [_label(root)]

    def walk(children: tuple[TreeNode, ...], prefix: str) -> None:
        for i, child in enumerate(children):
            last = i == len(children) - 1
            lines.append(f"{prefix}{'└─ ' if last else '├─ '}{_label(child)}")
            walk(child.children, prefix + ("   " if last else "│  "))

    walk(root.children, "")
    return lines


@click.command("tree")
@click.argument("task_id")
@TASKS_DIR_OPT
@JSON_OPT
def tree_cmd(task_id: str, tasks_dir: Path | None, as_json: bool) -> None:
    """Epic tree: children recursively, state/priority/title per node."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        node = build_tree(root.tasks_dir, task_id)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    if as_json:
        emit_json({"tree": node.to_dict()})
    else:
        for line in _tree_lines(node):
            click.echo(line)
        if not node.children:
            click.echo("  (no children)")


# link / unlink were absorbed into `rack transition --link/--unlink` (HATS-1030);
# this module keeps only the read verbs (tree / context / ls).


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
    click.echo("")
    click.echo(
        "tip: paths only by design — Read them directly; embed content with "
        "--with plan,summary,doc:<name> (per-doc cap --max-bytes)"
    )


@click.command("context")
@click.argument("task_id")
@click.option(
    "--with",
    "with_",
    multiple=True,
    help="Embed content: plan, summary, doc:<name> (comma-separable, repeatable).",
)
@click.option(
    "--max-bytes",
    default=DEFAULT_MAX_BYTES,
    show_default=True,
    help="Per-document ceiling for --with content; a cut is marked with the path.",
)
@TASKS_DIR_OPT
@JSON_OPT
def context_cmd(
    task_id: str,
    with_: tuple[str, ...],
    max_bytes: int,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """One-call discovery package: card, document paths, and a top-level links object."""
    selectors = [s.strip() for part in with_ for s in part.split(",") if s.strip()]
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        registry = load_registry_for(root.project_dir)
        pkg = build_context(
            root.tasks_dir, task_id, registry=registry, selectors=selectors, max_bytes=max_bytes
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    if as_json:
        emit_json(pkg.to_dict())
    else:
        _echo_context(pkg, root.tasks_dir)


# ----- ls -------------------------------------------------------------------------


@click.command("ls")
@click.option("--grep", default=None, help="Case-insensitive substring over title+description.")
@click.option("--tag", default=None, help="Exact tag match.")
@click.option("--state", default=None, help="Exact state match.")
@click.option("--parent", default=None, help="Direct children of this task id.")
@TASKS_DIR_OPT
@JSON_OPT
def ls_cmd(
    grep: str | None,
    tag: str | None,
    state: str | None,
    parent: str | None,
    tasks_dir: Path | None,
    as_json: bool,
) -> None:
    """Light backlog search: linear scan, filters AND-combined."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        rows = scan_cards(root.tasks_dir, grep=grep, tag=tag, state=state, parent=parent)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
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
