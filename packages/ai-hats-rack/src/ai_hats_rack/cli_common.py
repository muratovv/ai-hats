"""Shared CLI plumbing for ``rack`` command modules (HATS-1021).

Lives outside cli.py so subcommand modules (cli_doc, later K-children) can
reuse actor/output/root helpers without importing the command registry.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import click

from .resolver import RackRoot, resolve_root

# Same env contract as the tracker (string value is the shared contract).
ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_TASKS_DIR = "RACK_TASKS_DIR"

TASKS_DIR_OPT = click.option(
    "--tasks-dir",
    envvar=ENV_TASKS_DIR,
    default=None,
    type=click.Path(path_type=Path),
    help="Explicit override of the card-dirs root; default: walk-up project resolution.",
)
JSON_OPT = click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")


def actor() -> str:
    """Actor identity for the dispatch context: session > human fallback."""
    session = os.environ.get(ENV_SESSION_ID, "")
    if session:
        return f"session:{session}"
    try:
        return f"human:{getpass.getuser()}"
    except OSError:
        return "human:unknown"


def emit_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(as_json: bool, code: str, message: str, **details: Any) -> None:
    if as_json:
        emit_json({"error": {"code": code, "message": message, **details}})
    else:
        click.echo(f"error: {message}", err=True)
    sys.exit(1)


def resolved_root(tasks_dir: Path | None, caller_cwd: Path) -> RackRoot:
    """CLI-side root resolution: explicit override or validated walk-up.

    ``caller_cwd`` is captured ONCE at the command entry and threaded through —
    nothing below the CLI layer reads ``Path.cwd()`` (HATS-840 discipline).
    """
    return resolve_root(caller_cwd, tasks_dir)
