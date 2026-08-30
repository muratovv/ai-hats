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

from .cardschema import ExtrasForbiddenError, FieldValidationError
from .dispatch import OperationAborted
from .docstore import (
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
)
from .errors import ForeignProjectPinError, RackConfigError
from .fsm import InvalidTransitionError, UnknownStateError
from .kernel import (
    ForceRequiresReasonError,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
    UnroutableIdError,
)
from .linked import (
    AlreadyFoldedError,
    AlreadyLinkedError,
    EmptyGrepPatternError,
    ReciprocalLinkError,
    SelfLinkError,
)
from .models import CardLoadError, UnreadableWriteError
from .ops import AttachSourceError, OpParseError
from .registry import DerivedLinkKindError, UnknownLinkKindError
from .resolver import NoProjectRootError, RackRoot, resolve_root
from .roots_registry import DEFAULT_ROOTS_FILE, ENV_ROOTS_FILE, load_registered_roots
from .workspace import UnknownBacklogError

# Same env contract as the tracker (string value is the shared contract).
ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_TASKS_DIR = "RACK_TASKS_DIR"

#: Stated once, then rendered into both the ``--help`` line and the declaration
#: below, so the option and the reference page cannot say different things.
_TASKS_DIR_DEFAULT = "walk-up project resolution"

TASKS_DIR_OPT = click.option(
    "--tasks-dir",
    envvar=ENV_TASKS_DIR,
    default=None,
    type=click.Path(path_type=Path),
    help=f"Explicit override of the card-dirs root; default: {_TASKS_DIR_DEFAULT}.",
)
JSON_OPT = click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")


# Copied from ``ai_hats.env``, not imported: this distribution does not depend
# on ``ai_hats``, and that boundary is what keeps the rack installable on its own.
#: Every configurable path this distribution resolves through the environment.
#: ``RACK_TASKS_DIR`` reaches the resolver as ``--tasks-dir``'s ``envvar``; the
#: declaration adds nothing to that, it only makes the pair readable as data.
OVERRIDES: tuple[dict, ...] = (
    {
        "name": ENV_TASKS_DIR,
        "default": _TASKS_DIR_DEFAULT,
        "doc": "The card-dirs root every rack verb reads and writes.",
    },
    {
        "name": ENV_ROOTS_FILE,
        "default": f"`{DEFAULT_ROOTS_FILE}`",
        "doc": "The cross-project roots registry behind `--projects all`.",
    },
)


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
    UnroutableIdError: lambda e: (
        "unroutable_id",
        {"task_id": e.task_id, "prefix": e.prefix},
    ),
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
    AlreadyFoldedError: lambda e: (
        "already_folded",
        {"task_id": e.task_id, "current": e.current},
    ),
    AlreadyLinkedError: lambda e: (
        "already_linked",
        {"task_id": e.task_id, "kind": e.kind, "current": e.current},
    ),
    ReciprocalLinkError: lambda e: (
        "reciprocal_link",
        {"kind": e.kind, "source": e.source, "target": e.target},
    ),
    EmptyGrepPatternError: lambda e: ("empty_grep_pattern", {"field": e.field}),
    UnknownLinkKindError: lambda e: (
        "unknown_link_kind",
        {"kind": e.kind, "configured": list(e.configured)},
    ),
    DerivedLinkKindError: lambda e: ("derived_link_kind", {"kind": e.kind, "inverse": e.inverse}),
    NoProjectRootError: lambda e: ("no_project_root", {}),
    ForeignProjectPinError: lambda e: (
        "foreign_project_pin",
        {
            "pin": str(e.pin),
            "project_dir": str(e.project_dir),
            "ai_hats_dir": str(e.ai_hats_dir) if e.ai_hats_dir else None,
        },
    ),
    ForceRequiresReasonError: lambda e: ("invalid_request", {}),
    LockTimeoutError: lambda e: ("lock_timeout", {}),
    # Write-strict card-field refusal (required/choices/type/validator); the
    # RequiredFieldError subclass resolves here via the MRO.
    FieldValidationError: lambda e: ("invalid_field", {"field": e.field_name, **e.details}),
    ExtrasForbiddenError: lambda e: ("extras_forbidden", {"field": e.field_name}),
    # The write would not load back — refused at the persist chokepoint, so the
    # card on disk is untouched (HATS-1299).
    UnreadableWriteError: lambda e: ("unreadable_write", {"task_id": e.task_id}),
    # Present but unparseable — distinct from unknown_task, which means absent.
    CardLoadError: lambda e: ("card_load_failed", {"task_id": e.task_id, "path": str(e.path)}),
    # --backlog names no mounted backlog — a specific match ahead of the
    # RackConfigError catch-all below (nearest-MRO wins).
    UnknownBacklogError: lambda e: (
        "unknown_backlog",
        {"backlog": e.name, "mounted": list(e.mounted)},
    ),
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


def resolve_error(exc: Exception) -> tuple[str, str, dict[str, Any]] | None:
    """Typed ``(code, message, details)`` for a rack error, or ``None`` if the
    exception is truly unknown.

    The non-exiting core of :func:`handle_rack_error`: a batch read reuses it to
    fold a per-id failure into its output map instead of aborting the whole
    invocation (skip-and-continue).
    """
    handler = lookup_error_handler(type(exc))
    if handler is not None:
        code, details = handler(exc)
        return code, str(exc), details
    # ValueError is a builtin (not a RackError) but has always mapped here.
    if isinstance(exc, ValueError):
        return "invalid_request", str(exc), {}
    return None


def handle_rack_error(exc: Exception, as_json: bool) -> None:
    """Route a raised exception to its typed `fail`; re-raise the truly unknown."""
    resolved = resolve_error(exc)
    if resolved is None:
        raise exc
    code, message, details = resolved
    fail(as_json, code, message, **details)


def resolved_root(tasks_dir: Path | None, caller_cwd: Path) -> RackRoot:
    """CLI-side root resolution: explicit override or validated walk-up.

    ``caller_cwd`` is captured ONCE at the command entry and threaded through —
    nothing below the CLI layer reads ``Path.cwd()`` (HATS-840 discipline).
    """
    return resolve_root(caller_cwd, tasks_dir, environ=os.environ)


def resolve_roots(
    tasks_dir: Path | None, caller_cwd: Path, extra_roots: tuple[str, ...]
) -> list[RackRoot]:
    """The cross-project root set (HATS-1081): the CWD project first, then each
    explicit ``--root`` path (walk-up resolved), deduped by resolved ``tasks_dir``
    with first-seen order preserved. A bad ``--root`` path raises here (caller routes
    it to a typed error / skip).

    Keyed on the BACKLOG, not the anchor (HATS-1573): with ``--tasks-dir`` two
    roots can share a project_dir and still be different backlogs, and keying on
    the anchor would silently drop one of them."""
    roots = [resolved_root(tasks_dir, caller_cwd)]
    roots.extend(resolve_root(Path(p).expanduser(), None) for p in extra_roots)
    seen: set[Path] = set()
    unique: list[RackRoot] = []
    for r in roots:
        key = r.tasks_dir.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def resolve_project_roots(
    base_roots: list[RackRoot], projects: str
) -> tuple[list[RackRoot], list[str]]:
    """Append registry roots selected by ``projects`` (``"all"`` or a comma list of
    ``root_id``) to ``base_roots`` (HATS-1081, ``--projects`` = registry ∪ CWD).
    Skip-tolerant: a missing / broken registered root is skipped, never fatal (R5).
    Returns ``(roots, skipped_root_ids)``, deduped by resolved ``tasks_dir``."""
    registered = load_registered_roots()
    if projects != "all":
        wanted = {n.strip() for n in projects.split(",") if n.strip()}
        registered = [r for r in registered if r.name in wanted]
    roots = list(base_roots)
    seen = {r.tasks_dir.resolve() for r in roots}
    skipped: list[str] = []
    for path in registered:
        try:
            rr = resolve_root(path, None)
        except Exception:  # noqa: BLE001 — a broken root is skipped, not fatal (R5)
            skipped.append(path.name)
            continue
        key = rr.tasks_dir.resolve()
        if key not in seen:
            seen.add(key)
            roots.append(rr)
    return roots, skipped
