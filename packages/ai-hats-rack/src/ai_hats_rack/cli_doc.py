"""Doc-store display helpers (HATS-1021).

The doc verbs are gone: ``doc ls`` folded into show/context (HATS-1029), and
``doc freeze``/``doc rm`` were absorbed into ``rack transition --freeze/--rm``
(HATS-1030). What remains is the shared show-block rendering (name + absolute
path + mtime + frozen mark) reused across the read surfaces. Files are written
directly into ``tasks/<ID>/`` and read by path (rev4); every show/context
verifies pins.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from .docstore import DocInfo


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
