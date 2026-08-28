"""The steps every surface's dispatcher takes, in one copy — HATS-1868.

Four dispatchers wrote the same eight steps by hand, and the copies drifted:
HATS-1858's review found FOUR different behaviours for "the manifest is gone",
one per copy. After the fix the drift continued — the reduction to what a
surface can utter lived in three different places, wrapped around different
paths, so one of the three reduced a delivery refusal and two did not.

Here the flow exists once and a surface is what remains: a :class:`SurfaceProfile`
of names, plus the five things only it can answer.

What deliberately stays per-surface: **registration** (five foreign config
formats) and **reading the payload** (cline fans a command list into N calls,
codex a patch into one call per file). Those are drivers and translations, not
copies. This takes the control flow AROUND them.
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
    Dialect,
    HookCall,
    HookEvent,
    HookRow,
    SurfaceProfile,
    project_dir_from,
    reduce_to,
    relay_stderr,
    run_chain,
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
    """What the surface says this call IS: its own name, and what it binds to.

    Two facts behind one seam because they are one decision. codex's
    ``PermissionRequest`` is its own name for a call the chain judges as a
    PreToolUse, and agy delivers three arrivals nothing composed can bind to —
    a surface that could only report the name would leave the mapping to be
    guessed here, which is where it was guessed wrong before.
    """

    native: str
    #: ``None`` when nothing composed can bind to this arrival.
    event: HookEvent | None

    @classmethod
    def of(cls, native: str) -> Arrival:
        """The common case: the surface's own name IS the bindable name."""
        return cls(native, HookEvent.parse(native))


class SurfaceChannel(Protocol):
    """One surface's five answers. Everything else is :func:`dispatch`.

    ``speaks`` takes the arrival rather than reading ``profile.speaks`` because
    a dialect can be narrower on one arrival than on the surface as a whole:
    codex may ask only on ``PermissionRequest``, and it had to say so by hand
    before this existed.
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

    def speaks(self, arrival: Arrival) -> Dialect:
        """What this surface can utter on THIS arrival."""

    def emit(self, verdict: ChainVerdict, arrival: Arrival) -> int:
        """Say the verdict in the surface's own protocol; return its status."""


def dispatch(
    channel: SurfaceChannel,
    *,
    stdin=None,
    argv: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> int:
    """Judge one tool call for ``channel`` and answer in its own protocol.

    Every path reaches :func:`reduce_to` and :func:`relay_stderr`, including the
    delivery refusals — that was the drift: a refusal for a call that already
    ran skipped the reduction on two surfaces of three and cancelled what cannot
    be cancelled.
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
    reduced = reduce_to(channel.speaks(arrival), verdict)
    relay_stderr(reduced)
    return channel.emit(reduced, arrival)


def manifest_rows(
    path: Path,
    *,
    event: HookEvent,
    session_id: str,
    skills_root: Path,
) -> list[HookRow]:
    """The composed rows a session manifest holds, or why they could not be read.

    Everything here is a check three dispatchers already wrote for themselves,
    and one of them wrote none of the last two: opencode executed whatever
    string the manifest named until HATS-1858. Resolution of ``path`` and
    ``skills_root`` stays with the caller — that half is where the surfaces
    genuinely differ, and folding it in would need flags rather than data.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestUnresolved(f"cannot read session hook manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ManifestUnresolved(f"unsupported hook manifest at {path}")
    identity = data.get("session")
    if not isinstance(identity, dict) or identity.get("id") != session_id:
        raise ManifestUnresolved(f"hook manifest at {path} belongs to another session")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise ManifestUnresolved(f"hook manifest at {path} carries no hooks mapping")

    raw = hooks.get(event.value, [])
    rows: list[HookRow] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            raise ManifestUnresolved(f"hook entry is {type(entry).__name__}, not an object")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            raise ManifestUnresolved(f"malformed hook entry: {entry.get('tag', '<untagged>')}")
        resolved = Path(command).expanduser().resolve()
        if not resolved.is_relative_to(skills_root):
            raise ManifestUnresolved(f"hook command escapes the session skills mirror: {resolved}")
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise ManifestUnresolved(f"hook command is not an executable session file: {resolved}")
        rows.append(
            HookRow(
                command=resolved,
                matcher=str(entry.get("matcher", "")),
                tag=str(entry.get("tag") or command),
            )
        )
    return rows


__all__ = [
    "MANIFEST_VERSION",
    "Arrival",
    "ManifestUnresolved",
    "SurfaceChannel",
    "dispatch",
    "manifest_rows",
]
