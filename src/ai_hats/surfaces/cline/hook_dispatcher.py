"""Run composed ai-hats hooks for native Cline hook events."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping

from ai_hats.env import ENV_AI_HATS_DIR, ENV_SESSION_CACHE_DIR
from ai_hats_observe.trace import ENV_SESSION_ID

from ..hook_channel import (
    ChainDecision,
    ChainVerdict,
    HookEvent,
    HookRow,
    project_dir_from,
    reduce_to,
    run_chain,
    undeliverable,
    worded,
)
from .claude_hook_adapter import to_claude_hook_payloads
from .profile import PROFILE


class _ManifestError(RuntimeError):
    pass


def _resolved(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _load_manifest(environ: Mapping[str, str]) -> dict:
    session_id = environ.get(ENV_SESSION_ID, "")
    hats_dir = environ.get(ENV_AI_HATS_DIR, "")
    cache_dir = environ.get(ENV_SESSION_CACHE_DIR, "")
    if not session_id or not hats_dir or not cache_dir:
        raise _ManifestError(
            "incomplete hook identity: AI_HATS_SESSION_ID, AI_HATS_DIR, and "
            "AI_HATS_SESSION_CACHE_DIR are required"
        )
    path = Path(cache_dir) / "hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _ManifestError(f"cannot read session hook manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise _ManifestError(f"unsupported hook manifest at {path}")
    identity = data.get("session")
    if not isinstance(identity, dict):
        raise _ManifestError(f"hook manifest at {path} carries no session identity")
    if identity.get("id") != session_id:
        raise _ManifestError(
            f"session identity mismatch: env={session_id!r}, manifest={identity.get('id')!r}"
        )
    manifest_hats_dir = identity.get("ai_hats_dir")
    if not isinstance(manifest_hats_dir, str) or _resolved(manifest_hats_dir) != _resolved(
        hats_dir
    ):
        raise _ManifestError("manifest AI_HATS_DIR does not match the launcher")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise _ManifestError(f"hook manifest at {path} carries no hooks mapping")

    skills_root = (_resolved(cache_dir) / "skills").resolve()
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
                continue
            command = _resolved(entry["command"])
            if not command.is_relative_to(skills_root):
                raise _ManifestError(f"hook command escapes the session skills mirror: {command}")
            if not command.is_file() or not os.access(command, os.X_OK):
                raise _ManifestError(f"hook command is not an executable session file: {command}")
            entry["command"] = str(command)
    return data


def _emit(output: dict) -> None:
    sys.stdout.write(json.dumps(output) + "\n")


def _rows(manifest: dict, event: HookEvent) -> list[HookRow]:
    """The composed rows for ``event``, or a manifest error naming what is wrong."""
    raw = manifest["hooks"].get(event.value, [])
    rows: list[HookRow] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            raise _ManifestError(f"hook entry is {type(entry).__name__}, not an object")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            raise _ManifestError(f"malformed hook entry: {entry.get('tag', '<untagged>')}")
        rows.append(
            HookRow(
                command=Path(command),
                matcher=str(entry.get("matcher", "")),
                tag=str(entry.get("tag") or command),
            )
        )
    return rows


def _undeliverable(reason: str, event: HookEvent | None = None) -> ChainVerdict:
    """A gate ai-hats could not deliver refuses, naming a way past that works.

    Every one of these used to answer ``{"cancel": false}``: the tool call went
    through and the only trace was a line on stderr nobody reads mid-session.

    ``event`` travels so a refusal for a call that ALREADY ran can still be
    reduced to something the reader sees, rather than cancelling what cannot be
    cancelled.
    """
    return undeliverable(
        f"ai-hats-cline-hook: {reason}",
        event=event,
        project_dir=project_dir_from(os.environ),
    )


def _reply(raw: ChainVerdict) -> int:
    """Reduce to what cline can utter, then say it.

    The reduction happens HERE so every path reaches it — a delivery refusal for
    a call that already ran used to skip it and cancel what cannot be cancelled.
    """
    verdict = reduce_to(PROFILE.speaks, raw)
    if verdict.decision in (ChainDecision.DENY, ChainDecision.ASK):
        said = worded(verdict)
        # Also on stderr: the agent reads errorMessage, an operator reading the
        # session log afterwards reads this, and the bypass journal rides here.
        sys.stderr.write(said + "\n")
        if verdict.stderr:
            sys.stderr.write(verdict.stderr)
        _emit({"cancel": True, "errorMessage": said})
        return 0
    output: dict[str, object] = {"cancel": False}
    if verdict.nudges:
        output["contextModification"] = "\n".join(n.text for n in verdict.nudges)
    _emit(output)
    return 0


def dispatch_hook(event: str, *, stdin=None) -> int:
    """Run the composed chain for one Cline event and emit Cline-native JSON."""
    bound = HookEvent.parse(event)
    if bound is None:
        # Nothing composed can bind here, so there is no gate to have missed.
        sys.stderr.write(f"ai-hats-cline-hook: unsupported event: {event}\n")
        _emit({"cancel": False})
        return 0
    source = stdin if stdin is not None else sys.stdin
    try:
        payload = json.loads(source.read())
    except (OSError, ValueError) as exc:
        return _reply(_undeliverable(f"invalid payload: {exc}", bound))
    if not isinstance(payload, dict):
        return _reply(_undeliverable("payload is not an object", bound))
    try:
        rows = _rows(_load_manifest(os.environ), bound)
    except _ManifestError as exc:
        return _reply(_undeliverable(str(exc), bound))

    return _reply(
        run_chain(
            PROFILE,
            event=bound,
            rows=rows,
            payloads=to_claude_hook_payloads(payload, event),
            project_dir=project_dir_from(os.environ),
        )
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m ai_hats.surfaces.cline.hook_dispatcher EVENT\n")
        raise SystemExit(2)
    raise SystemExit(dispatch_hook(sys.argv[1]))


if __name__ == "__main__":
    main()


__all__ = ["dispatch_hook", "main"]
