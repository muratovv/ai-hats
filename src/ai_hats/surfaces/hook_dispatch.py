"""One dispatcher flow for every surface — read the call, run its gates, answer.

A surface differs from its siblings in FOUR places: how a call **arrives**,
where its composed **rows** come from, how the stdin document **reads** as
calls, and how a verdict is **emitted** in its own protocol. :func:`dispatch` is
the order those four go in; everything between them — the refusal for a gate set
that never resolved, the reduction to what the surface can utter, the chain
itself, the stderr relay — lives here once.

What a surface can UTTER and what status it EXITS with are not among the four.
Both are known before a hook arrives, so both are rows of
:class:`SurfaceProfile`: ``speaks`` / ``speaks_on``, and ``imposed_status`` /
``forwards_hook_status``.

Registration stays per-surface — five foreign config formats — and so does the
translation inside :meth:`SurfaceChannel.read`. Those are drivers, not copies of
a flow.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .hook_channel import (
    ChainDecision,
    ChainVerdict,
    HookCall,
    HookEvent,
    HookRow,
    SurfaceProfile,
    project_dir_from,
    reduce_to,
    relay_stderr,
    run_chain,
    status_for,
    undeliverable,
)


#: The manifest shape every surface writes and reads.
MANIFEST_VERSION = 1


class ManifestUnresolved(RuntimeError):
    """The composed rows could not be produced — no gate ran, and none can.

    The one exception :meth:`SurfaceChannel.rows` may raise, so what went wrong
    reaches the human through a delivery refusal that names the hatch, rather
    than as a traceback the surface turns into a pass.
    """


@dataclass(frozen=True)
class Arrival:
    """What this call is: the surface's own event name, and what it binds to.

    One value rather than two answers, because a surface decides both at once —
    and only it can. ``PermissionRequest`` is codex's own name for a call the
    chain judges as a PreToolUse; three of agy's arrivals bind to nothing.
    """

    #: The surface's own name for the EVENT — ``PreToolUse`` on claude,
    #: ``PermissionRequest`` on codex. Neither a provider nor a tool name.
    native: str
    #: The bindable event ``native`` maps to, or ``None`` when a composed hook
    #: cannot be bound to this arrival at all.
    event: HookEvent | None

    @classmethod
    def of(cls, native: str) -> Arrival:
        """The common case: the surface's own name IS the bindable name."""
        return cls(native, HookEvent.parse(native))


class SurfaceChannel(Protocol):
    """One surface's four answers. Everything else is :func:`dispatch`.

    What a surface can UTTER and what status it exits with are not among them:
    both are known before a hook arrives, so both are rows of
    :class:`SurfaceProfile` — ``speaks`` / ``speaks_on`` and ``imposed_status``
    / ``forwards_hook_status``. A channel writes its document; the status is
    derived from the verdict by :func:`status_for`.
    """

    profile: SurfaceProfile

    def arrival(self, payload: dict, argv: Sequence[str]) -> Arrival:
        """What this call is — from the document, from argv, or from both."""

    def rows(self, environ: Mapping[str, str], event: HookEvent) -> Sequence[HookRow]:
        """The composed rows for ``event``. Raises :class:`ManifestUnresolved`."""

    def read(self, payload: dict, arrival: Arrival) -> Sequence[HookCall]:
        """The document as the calls the chain judges — one, or several.

        Where the call sits inside it is this method's business: opencode is
        handed a request envelope with the call under a key, and cline fans one
        document into a call per command.
        """

    def emit(self, verdict: ChainVerdict, arrival: Arrival) -> None:
        """Write the verdict as this surface's own document."""


def dispatch(
    channel: SurfaceChannel,
    *,
    stdin=None,
    argv: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
    hook_environ: Mapping[str, str] | None = None,
) -> int:
    """Judge one tool call for ``channel`` and answer in its own protocol.

    Every path reaches :func:`reduce_to` and :func:`relay_stderr`, including the
    delivery refusals — that was the drift: a refusal for a call that already
    ran skipped the reduction on two surfaces of three and cancelled what cannot
    be cancelled.

    ``hook_environ`` is what the gates inherit; a dispatcher that is not inside
    the session it judges for hands the session's in.
    """
    env = environ if environ is not None else os.environ
    source = stdin if stdin is not None else sys.stdin

    # Whatever stdin held, handed on whole: what counts as the CALL inside it is
    # `read`'s business, and opencode's is one key down inside an envelope.
    payload: dict = {}
    unreadable = ""
    try:
        parsed = json.loads(source.read())
    except (OSError, ValueError) as exc:
        unreadable = f"invalid payload: {exc}"
    else:
        if isinstance(parsed, dict):
            payload = parsed
        else:
            unreadable = f"payload is {type(parsed).__name__}, not an object"

    arrival = channel.arrival(payload, argv)
    if unreadable:
        return _refuse(channel, arrival, unreadable, env)
    if arrival.event is None or arrival.native not in channel.profile.native_events:
        # Nothing composed can bind here, so no gate was missed.
        return _say(
            channel, ChainVerdict(decision=ChainDecision.ALLOW, event=arrival.event), arrival
        )
    try:
        rows = channel.rows(env, arrival.event)
    except ManifestUnresolved as exc:
        return _refuse(channel, arrival, str(exc), env)
    return _say(
        channel,
        run_chain(
            channel.profile,
            event=arrival.event,
            rows=rows,
            calls=channel.read(payload, arrival),
            project_dir=project_dir_from(env),
            environ=env,
            hook_environ=hook_environ,
        ),
        arrival,
    )


def _refuse(
    channel: SurfaceChannel, arrival: Arrival, reason: str, environ: Mapping[str, str]
) -> int:
    """A gate set that never resolved, in this surface's own words.

    Through the one constructor that also honours the hatch its refusal names —
    a refusal every surface words identically and only one of them can open is
    the failure this channel exists to remove.
    """
    return _say(
        channel,
        undeliverable(
            f"ai-hats-{channel.profile.label}-hook: {reason}",
            event=arrival.event,
            project_dir=project_dir_from(environ),
            environ=environ,
        ),
        arrival,
    )


def _say(channel: SurfaceChannel, verdict: ChainVerdict, arrival: Arrival) -> int:
    profile = channel.profile
    reduced = reduce_to(profile.dialect(arrival.native), verdict)
    relay_stderr(reduced)
    channel.emit(reduced, arrival)
    return status_for(profile, reduced)


def _require(held: object, reason: str) -> None:
    """Refuse the whole gate set unless ``held``.

    One raise site, so every check reads as the thing it asserts rather than as
    an inverted branch around a throw.
    """
    if not held:
        raise ManifestUnresolved(reason)


def _document(path: Path) -> dict:
    """The manifest at ``path``, parsed and known to be one of ours."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestUnresolved(f"cannot read session hook manifest {path}: {exc}") from exc
    _require(
        isinstance(data, dict) and data.get("version") == MANIFEST_VERSION,
        f"unsupported hook manifest at {path}",
    )
    return data


def _row(entry: object, skills_root: Path) -> HookRow:
    """One manifest entry as a row, or a refusal naming what is wrong with it.

    A row that cannot be read RAISES rather than being skipped: dropping it is
    indistinguishable from never having declared the gate.
    """
    _require(isinstance(entry, dict), f"hook entry is {type(entry).__name__}, not an object")
    command = entry.get("command")  # type: ignore[union-attr]
    _require(
        isinstance(command, str) and command,
        f"malformed hook entry: {entry.get('tag', '<untagged>')}",  # type: ignore[union-attr]
    )
    resolved = Path(str(command)).expanduser().resolve()
    _require(
        resolved.is_relative_to(skills_root),
        f"hook command escapes the session skills mirror: {resolved}",
    )
    _require(
        resolved.is_file() and os.access(resolved, os.X_OK),
        f"hook command is not an executable session file: {resolved}",
    )
    return HookRow(
        command=resolved,
        matcher=str(entry.get("matcher", "")),  # type: ignore[union-attr]
        tag=str(entry.get("tag") or command),  # type: ignore[union-attr]
    )


def manifest_rows(
    path: Path,
    *,
    event: HookEvent,
    session_id: str,
    skills_root: Path,
) -> list[HookRow]:
    """The composed rows a session manifest holds for ``event``.

    Every check here guards one way a manifest can name a gate that must not
    run: another session's, an unreadable document, a command outside the
    session mirror, a file without the executable bit.

    Resolving ``path`` and ``skills_root`` stays with the caller — that half is
    where surfaces genuinely differ, and folding it in would take flags.
    """
    data = _document(path)
    identity = data.get("session")
    _require(
        isinstance(identity, dict) and identity.get("id") == session_id,
        f"hook manifest at {path} belongs to another session",
    )
    hooks = data.get("hooks")
    _require(isinstance(hooks, dict), f"hook manifest at {path} carries no hooks mapping")
    raw = hooks.get(event.value, [])  # type: ignore[union-attr]
    return [_row(entry, skills_root) for entry in (raw if isinstance(raw, list) else [])]


__all__ = [
    "MANIFEST_VERSION",
    "Arrival",
    "ManifestUnresolved",
    "SurfaceChannel",
    "dispatch",
    "manifest_rows",
]
