"""``rack doc`` — the three-verb doc-store surface: ls / freeze / rm (HATS-1021).

No ``put``/``cat``: files are written directly into ``tasks/<ID>/`` and read
by path (rev4). ``verify`` is not a verb — every ls/show verifies pins.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from .cli_common import JSON_OPT, TASKS_DIR_OPT, actor, emit_json, fail, resolved_root
from .docstore import (
    DocInfo,
    DocStore,
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
)
from .kernel import LockTimeoutError, UnknownTaskError
from .resolver import NoProjectRootError


def handle_doc_error(exc: Exception, as_json: bool) -> None:
    """Typed, actionable failures for doc/root ops (mirror of cli._handle_kernel_error)."""
    if isinstance(exc, UnknownTaskError):
        fail(as_json, "unknown_task", str(exc), task_id=exc.task_id)
    if isinstance(exc, UnknownDocumentError):
        fail(as_json, "unknown_document", str(exc), task_id=exc.task_id, name=exc.name)
    if isinstance(exc, DocumentNameError):
        fail(as_json, "invalid_document_name", str(exc), name=exc.name)
    if isinstance(exc, FrozenDocumentError):
        fail(as_json, "frozen_document", str(exc), task_id=exc.task_id, name=exc.name)
    if isinstance(exc, FrozenPinDriftError):
        fail(
            as_json,
            "frozen_pin_drift",
            str(exc),
            task_id=exc.task_id,
            name=exc.name,
            pinned_digest=exc.pinned,
            current_digest=exc.current,
        )
    if isinstance(exc, NoProjectRootError):
        fail(as_json, "no_project_root", str(exc))
    if isinstance(exc, LockTimeoutError):
        fail(as_json, "lock_timeout", str(exc))
    raise exc


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
    """The show-block: name + ABSOLUTE path + mtime + frozen mark. Content is
    never inlined — the agent Reads by the printed path (discovery model)."""
    click.echo(f"{indent}Documents ({card_dir.absolute()}):")
    if not docs:
        click.echo(f"{indent}  (none — write files into this directory to add)")
        return
    rows = [[d.name, str(d.path), _mtime_human(d.mtime), _frozen_mark(d)] for d in docs]
    for line in _columns(rows, indent + "  "):
        click.echo(line)


@click.group()
def doc() -> None:
    """Doc store: fs-as-truth view over tasks/<ID>/ (write = plain file write)."""


@doc.command("ls")
@click.argument("task_id")
@TASKS_DIR_OPT
@JSON_OPT
def ls(task_id: str, tasks_dir: Path | None, as_json: bool) -> None:
    """List documents: live scan + digests; drifted frozen pins fail (rc 1)."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        docs = DocStore(root.tasks_dir).scan(task_id)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_doc_error(exc, as_json)
        return
    drifted = [d.name for d in docs if d.drift]
    if as_json:
        emit_json(
            {
                "task_id": task_id,
                "dir": str(DocStore(root.tasks_dir).card_dir(task_id).absolute()),
                "documents": [d.to_dict() for d in docs],
                "drifted": drifted,
            }
        )
    else:
        if not docs:
            click.echo(f"No documents in {DocStore(root.tasks_dir).card_dir(task_id).absolute()}")
        else:
            rows = [
                [d.name, str(d.path), _mtime_human(d.mtime), d.digest or "—", _frozen_mark(d)]
                for d in docs
            ]
            for line in _columns(rows):
                click.echo(line)
        if drifted:
            click.echo(
                f"error: frozen pin drift on: {', '.join(drifted)} — restore the "
                "content or re-pin deliberately (rack doc freeze --refreeze)",
                err=True,
            )
    if drifted:
        raise SystemExit(1)


@doc.command()
@click.argument("task_id")
@click.argument("name")
@click.option(
    "--refreeze",
    is_flag=True,
    help="Accept changed content under an existing pin (re-pin the new digest).",
)
@TASKS_DIR_OPT
@JSON_OPT
def freeze(task_id: str, name: str, refreeze: bool, tasks_dir: Path | None, as_json: bool) -> None:
    """Pin {name, digest, frozen} into task.yaml; later drift = error on ls/show."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        info = DocStore(root.tasks_dir).freeze(task_id, name, actor=actor(), refreeze=refreeze)
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_doc_error(exc, as_json)
        return
    if as_json:
        emit_json({"task_id": task_id, "document": info.to_dict()})
    else:
        click.echo(f"Frozen: {task_id}/{info.name} ({info.digest})")


@doc.command()
@click.argument("task_id")
@click.argument("name")
@click.option(
    "--ack-frozen",
    is_flag=True,
    help="Confirm removing a FROZEN document together with its pin.",
)
@TASKS_DIR_OPT
@JSON_OPT
def rm(task_id: str, name: str, ack_frozen: bool, tasks_dir: Path | None, as_json: bool) -> None:
    """Remove a document — moved to a tmp trash session, never hard-deleted."""
    try:
        root = resolved_root(tasks_dir, Path.cwd())
        result = DocStore(root.tasks_dir).remove(
            task_id, name, actor=actor(), ack_frozen=ack_frozen
        )
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_doc_error(exc, as_json)
        return
    if as_json:
        emit_json(
            {
                "task_id": task_id,
                "removed": {
                    "name": result.name,
                    "trashed_to": str(result.trashed_to) if result.trashed_to else None,
                    "pin_removed": result.pin_removed,
                },
            }
        )
    else:
        where = (
            f" (recoverable: {result.trashed_to})" if result.trashed_to else " (no file on disk)"
        )
        click.echo(f"Removed: {task_id}/{result.name}{where}")
