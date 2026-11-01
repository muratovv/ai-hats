"""Join (ADR-0035 D6): one identity, one row; ``refuse`` beats ``warn``; the first keeps the slot."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path

from ai_hats_wt import WT_TEARDOWN_EVENTS

from ..diagnostics import Diagnostic, Level
from ..surfaces.hook_channel import HookEvent
from ..surfaces.plan import Executable, ExternalHook, Hooks, OnError, RuntimeHook
from .errors import ProcessingError
from .fold import Folded
from .names import Tree, resolve
from .parse import Hook, entity_type

_CONSENT_APP = "consent_gate"


@dataclass(frozen=True)
class _Joined:
    hook: Hook
    declared_by: str
    #: The registry key a consent point names. ``None`` for every other kind.
    operation: str | None


def _identity(hook: Hook, operation: str | None) -> tuple:
    """The join key. ``run`` is the payload path from the layer root, except consent."""
    run = hook.run_key
    if hook.kind == "workflow":
        return ("workflow", hook.app, hook.object, hook.at, run)
    if hook.kind == "runtime":
        return ("runtime", hook.at, hook.matcher, run)
    if hook.kind == "git":
        return ("git", hook.at, run)
    if hook.kind == "worktree":
        return ("worktree", hook.at, hook.on, run)
    return ("consent", operation, hook.at)


def _operation(tree: Tree, hook: Hook) -> str | None:
    if hook.kind != "consent":
        return None
    if hook.operation is None:
        raise ProcessingError(f"{hook.path}: a consent hook requires 'operation'")
    try:
        name = resolve(tree, hook.operation)
    except ProcessingError as exc:
        raise ProcessingError(f"{hook.path}: {exc}") from exc
    entity = tree.entities[name]
    if entity_type(entity) != "operation":
        raise ProcessingError(
            f"{hook.path}: operation {hook.operation!r} names {name}, a(n) {entity_type(entity)}"
        )
    return name.split("::")[-1]


def _stricter(kept: Hook, incoming: Hook) -> Hook:
    if incoming.on_error == "refuse" and kept.on_error != "refuse":
        return replace(kept, on_error="refuse")
    return kept


def join(folded: Folded, tree: Tree, diagnostics: list[Diagnostic]) -> tuple[_Joined, ...]:
    """The surviving hook terms, one row per identity, in first-seen order."""
    brought = {row.term: row.brought_by for row in folded.trace if row.removed_by is None}
    slots: dict[tuple, int] = {}
    rows: list[_Joined] = []
    for name in folded.members:
        hook = tree.entities[name]
        if not isinstance(hook, Hook):
            continue
        operation = _operation(tree, hook)
        key = _identity(hook, operation)
        if key not in slots:
            slots[key] = len(rows)
            rows.append(_Joined(hook, brought.get(name, name), operation))
            continue
        kept = rows[slots[key]]
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"{name} repeats {kept.hook.name}; the first declaration keeps the slot",
                where=hook.path,
            )
        )
        rows[slots[key]] = _Joined(_stricter(kept.hook, hook), kept.declared_by, operation)
    return tuple(rows)


def _executable(path: Path) -> Executable:
    return Executable(
        path=path.resolve(), content_digest=hashlib.sha256(path.read_bytes()).hexdigest()
    )


def _runnable(row: _Joined, diagnostics: list[Diagnostic]) -> Executable | None:
    """Omit a payload that is not executable, so the plan lists hooks that can run."""
    path = row.hook.run
    if path is None:
        return None
    if not os.access(path, os.X_OK):
        diagnostics.append(
            Diagnostic(
                Level.WARN,
                f"{row.hook.name}: {row.hook.run_key} is not executable; this hook will not run",
                where=row.hook.path,
            )
        )
        return None
    return _executable(path)


def _external(
    row: _Joined, *, app: str, at: str, run: Executable | None, on_error: str | None
) -> ExternalHook:
    return ExternalHook(
        app=app,
        object=row.operation if row.hook.kind == "consent" else row.hook.object,
        at=at,
        run=run,
        on_error=None if on_error is None else OnError(on_error),
        declared_by=row.declared_by,
    )


def project(folded: Folded, tree: Tree, diagnostics: list[Diagnostic]) -> Hooks:
    """Join the surviving hooks and lay them into the plan's two channels."""
    runtime: list[RuntimeHook] = []
    external: list[ExternalHook] = []
    for row in join(folded, tree, diagnostics):
        hook = row.hook
        if hook.kind == "consent":
            external.append(_external(row, app=_CONSENT_APP, at=hook.at, run=None, on_error=None))
            continue
        run = _runnable(row, diagnostics)
        if run is None:
            continue
        if hook.kind == "runtime":
            event = HookEvent.parse(hook.at)
            if event is None:
                raise ProcessingError(f"{hook.path}: runtime at {hook.at!r} is not a hook event")
            runtime.append(RuntimeHook(at=event, matcher=hook.matcher or "", run=run))
            continue
        if hook.kind == "git":
            external.append(_external(row, app="git", at=hook.at, run=run, on_error=None))
            continue
        if hook.kind == "workflow":
            external.append(
                _external(row, app=hook.app or "", at=hook.at, run=run, on_error=hook.on_error)
            )
            continue
        points = hook.on or WT_TEARDOWN_EVENTS if hook.at == "wt_out" else ()
        if hook.at == "wt_in":
            external.append(_external(row, app="wt", at="create", run=run, on_error=None))
            continue
        for event in points:
            external.append(
                _external(row, app="wt", at=f"teardown[{event}]", run=run, on_error=None)
            )
    return Hooks(runtime=tuple(runtime), external=tuple(external))
