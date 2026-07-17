"""``rack`` linked-task verbs: tree / link / unlink / context / ls (HATS-1024).

``context`` is THE one-call discovery package — card + document paths +
parent/depends_on/related/children — replacing the 10-call, 209 851-char
baseline F4 walk. Content rides only behind an explicit ``--with`` selector,
capped per document by ``--max-bytes`` with a marked, path-carrying cut.
"""

from __future__ import annotations

from pathlib import Path

import click

from . import linked
from .cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, fail, resolved_root

# shared table/mtime style — doc rows look the same on every rack surface
from .cli_doc import _columns, _mtime_human, echo_documents
from .docstore import UnknownDocumentError
from .kernel import LockTimeoutError, UnknownTaskError
from .linked import (
    DEFAULT_MAX_BYTES,
    ContextPackage,
    LinkedBlock,
    SelfLinkError,
    TreeNode,
    UnknownLinkKindError,
    UnknownSelectorError,
    build_context,
    build_tree,
    canonical_kind,
    scan_cards,
)
from .resolver import NoProjectRootError


def handle_linked_error(exc: Exception, as_json: bool) -> None:
    """Typed, actionable failures (mirror of cli._handle_kernel_error)."""
    if isinstance(exc, UnknownTaskError):
        fail(as_json, "unknown_task", str(exc), task_id=exc.task_id)
    if isinstance(exc, SelfLinkError):
        fail(as_json, "self_link", str(exc), task_id=exc.task_id)
    if isinstance(exc, UnknownLinkKindError):
        fail(as_json, "unknown_link_kind", str(exc), kind=exc.kind)
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


# ----- link / unlink -------------------------------------------------------------


@click.command("link")
@click.argument("task_id")
@click.argument("target")
@click.option(
    "--kind",
    type=click.Choice(["related", "depends"]),
    default="related",
    show_default=True,
    help="Card field to add TARGET to (depends → depends_on).",
)
@TASKS_DIR_OPT
@JSON_OPT
def link_cmd(task_id: str, target: str, kind: str, tasks_dir: Path | None, as_json: bool) -> None:
    """Link TARGET into TASK_ID's card (task-locked single persist; idempotent)."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        result = linked.link(root.tasks_dir, task_id, target, kind, actor=actor())
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    field = canonical_kind(kind)
    if as_json:
        emit_json({**result.to_dict(), "kind": field})
    elif result.changed:
        click.echo(f"Linked: {task_id} {field} {target}")
    else:
        click.echo(f"Already linked: {task_id} {field} {target} (no-op)")


@click.command("unlink")
@click.argument("task_id")
@click.argument("target")
@click.option(
    "--kind",
    type=click.Choice(["related", "depends"]),
    default=None,
    help="Only this field; default removes TARGET from both.",
)
@TASKS_DIR_OPT
@JSON_OPT
def unlink_cmd(
    task_id: str, target: str, kind: str | None, tasks_dir: Path | None, as_json: bool
) -> None:
    """Remove TARGET from TASK_ID's link fields (idempotent; dangling ok)."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        result = linked.unlink(  # safe-delete: ok card-field op, no fs delete
            root.tasks_dir, task_id, target, kind, actor=actor()
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_linked_error(exc, as_json)
        return
    if as_json:
        emit_json(result.to_dict())
    elif result.changed:
        click.echo(f"Unlinked: {task_id} {', '.join(result.kinds)} {target}")
    else:
        click.echo(f"Not linked: {task_id} — {target} (no-op)")


# ----- context -------------------------------------------------------------------


def _echo_block(header: str, blocks: tuple[LinkedBlock, ...]) -> None:
    click.echo(f"  {header}:")
    for block in blocks:
        res = f" — resolution: {block.resolution}" if block.resolution else ""
        click.echo(f"    {block.id} [{block.state}] {block.title}{res}")
        if block.docs:
            rows = [[ref.name, str(ref.path), _mtime_human(ref.mtime)] for ref in block.docs]
            for line in _columns(rows, "      "):
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
    if pkg.parent:
        click.echo("")
        _echo_block("Parent", (pkg.parent,))
    if pkg.depends_on:
        click.echo("")
        _echo_block("Depends on", pkg.depends_on)
    if pkg.related:
        click.echo("")
        _echo_block("Related", pkg.related)
    if pkg.children:
        click.echo("")
        click.echo("  Children:")
        rows = [[c.id, f"[{c.state}]", c.priority, c.title] for c in pkg.children]
        for line in _columns(rows, "    "):
            click.echo(line)
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
    """One-call discovery package: card, document paths, parent, links, children."""
    selectors = [s.strip() for part in with_ for s in part.split(",") if s.strip()]
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        pkg = build_context(root.tasks_dir, task_id, selectors=selectors, max_bytes=max_bytes)
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
