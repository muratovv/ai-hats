"""Shared CLI plumbing for ``rack`` command modules (HATS-1021).

Lives outside cli.py so subcommand modules (cli_context) can reuse
actor/output/root helpers without importing the command registry.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import click

from .cardschema import FieldValidationError
from .dispatch import OperationAborted
from .docstore import (
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
)
from .errors import RackConfigError
from .fsm import InvalidTransitionError, UnknownStateError
from .kernel import (
    ForceRequiresReasonError,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
)
from .linked import SelfLinkError
from .ops import AttachSourceError, OpParseError
from .registry import DerivedLinkKindError, UnknownLinkKindError
from .resolver import NoProjectRootError, RackRoot, resolve_root

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


# ----- typed error surface (HATS-1033) ---------------------------------------

# ONE dispatch table replaces the per-command isinstance chains; each RackError
# subclass resolves via MRO to a (code, details) handler — test_error_surface.py
# pins that every subclass stays covered.
_ErrorHandler = Callable[[Any], "tuple[str, dict[str, Any]]"]

_ERROR_HANDLERS: dict[type, _ErrorHandler] = {
    InvalidTransitionError: lambda e: (
        "invalid_transition",
        {
            "task_id": e.task_id,
            "from_state": e.from_state,
            "to_state": e.to_state,
            "legal_edges": list(e.allowed),
        },
    ),
    UnknownStateError: lambda e: ("unknown_state", {"known_states": list(e.known)}),
    OperationAborted: lambda e: ("aborted", {"subscriber": e.subscriber, "reason": e.reason}),
    UnknownTaskError: lambda e: ("unknown_task", {"task_id": e.task_id}),
    TaskExistsError: lambda e: ("task_exists", {"task_id": e.task_id}),
    OpParseError: lambda e: ("invalid_ops", {}),
    AttachSourceError: lambda e: ("attach_source", {"src": e.src}),
    DocumentNameError: lambda e: ("invalid_document_name", {"name": e.name}),
    UnknownDocumentError: lambda e: ("unknown_document", {"task_id": e.task_id, "name": e.name}),
    FrozenDocumentError: lambda e: ("frozen_document", {"task_id": e.task_id, "name": e.name}),
    FrozenPinDriftError: lambda e: (
        "frozen_pin_drift",
        {
            "task_id": e.task_id,
            "name": e.name,
            "pinned_digest": e.pinned,
            "current_digest": e.current,
        },
    ),
    SelfLinkError: lambda e: ("self_link", {"task_id": e.task_id}),
    UnknownLinkKindError: lambda e: (
        "unknown_link_kind",
        {"kind": e.kind, "configured": list(e.configured)},
    ),
    DerivedLinkKindError: lambda e: ("derived_link_kind", {"kind": e.kind, "inverse": e.inverse}),
    NoProjectRootError: lambda e: ("no_project_root", {}),
    ForceRequiresReasonError: lambda e: ("invalid_request", {}),
    LockTimeoutError: lambda e: ("lock_timeout", {}),
    # Write-strict card-field refusal (required/choices/type/validator); the
    # RequiredFieldError subclass resolves here via the MRO.
    FieldValidationError: lambda e: ("invalid_field", {"field": e.field_name, **e.details}),
    # Structural "a loaded config file is malformed" invariants — one internal
    # marker for the whole RackConfigError subtree (matched via MRO).
    RackConfigError: lambda e: ("internal", {}),
}


def lookup_error_handler(exc_type: type) -> _ErrorHandler | None:
    """Nearest handler along the MRO — subtype-aware, as the old isinstance was."""
    for klass in exc_type.__mro__:
        handler = _ERROR_HANDLERS.get(klass)
        if handler is not None:
            return handler
    return None


def handle_rack_error(exc: Exception, as_json: bool) -> None:
    """Route a raised exception to its typed `fail`; re-raise the truly unknown."""
    handler = lookup_error_handler(type(exc))
    if handler is not None:
        code, details = handler(exc)
        fail(as_json, code, str(exc), **details)
        return
    # ValueError is a builtin (not a RackError) but has always mapped here.
    if isinstance(exc, ValueError):
        fail(as_json, "invalid_request", str(exc))
        return
    raise exc


def resolved_root(tasks_dir: Path | None, caller_cwd: Path) -> RackRoot:
    """CLI-side root resolution: explicit override or validated walk-up.

    ``caller_cwd`` is captured ONCE at the command entry and threaded through —
    nothing below the CLI layer reads ``Path.cwd()`` (HATS-840 discipline).
    """
    return resolve_root(caller_cwd, tasks_dir)
