"""Run the composed chain for one OpenCode tool event and marshal the verdict.

The plugin that calls this is JavaScript, so it cannot hold a verdict — it is
handed one as a document (:func:`hook_channel.to_wire`) and marshals it back.
Before this existed the plugin derived its own verdict from an exit code and
understood exactly two shapes, so seven of the eight shipped gates could refuse
a call and be waved through.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping

from ai_hats.env import ENV_SESSION_CACHE_DIR
from ai_hats_observe.trace import ENV_SESSION_ID

from ..hook_channel import (
    ChainDecision,
    ChainVerdict,
    HookEvent,
    HookRow,
    project_dir_from,
    reduce_to,
    relay_stderr,
    run_chain,
    speak_args,
    to_wire,
    undeliverable,
)
from .profile import PROFILE

MANIFEST_VERSION = 1


class _ManifestError(RuntimeError):
    pass


def _load_rows(environ: Mapping[str, str], event: HookEvent) -> list[HookRow]:
    session_id = environ.get(ENV_SESSION_ID, "")
    cache_dir = environ.get(ENV_SESSION_CACHE_DIR, "")
    if not session_id or not cache_dir:
        raise _ManifestError(
            f"incomplete hook identity: {ENV_SESSION_ID} and {ENV_SESSION_CACHE_DIR} are required"
        )
    path = PROFILE.manifest_path(Path(cache_dir))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _ManifestError(f"cannot read session hook manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise _ManifestError(f"unsupported hook manifest at {path}")
    identity = data.get("session")
    if not isinstance(identity, dict) or identity.get("id") != session_id:
        raise _ManifestError(f"hook manifest at {path} belongs to another session")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise _ManifestError(f"hook manifest at {path} carries no hooks mapping")
    # Where this surface's mirror sits is a row of the profile, not a path
    # spelled again here; codex and cline have refused an escaping command since
    # they were written, and this one executed whatever string it was handed.
    skills_root = PROFILE.skills_root(Path(cache_dir))
    if skills_root is None:
        raise _ManifestError(f"{PROFILE.label} declares no session skills mirror")
    skills_root = skills_root.expanduser().resolve()
    raw = hooks.get(event.value, [])
    rows: list[HookRow] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            raise _ManifestError(f"hook entry is {type(entry).__name__}, not an object")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            raise _ManifestError(f"malformed hook entry: {entry.get('tag', '<untagged>')}")
        resolved = Path(command).expanduser().resolve()
        if not resolved.is_relative_to(skills_root):
            raise _ManifestError(f"hook command escapes the session skills mirror: {resolved}")
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise _ManifestError(f"hook command is not an executable session file: {resolved}")
        rows.append(
            HookRow(
                command=resolved,
                matcher=str(entry.get("matcher", "")),
                tag=str(entry.get("tag") or command),
            )
        )
    return rows


def dispatch_hook(*, stdin=None, environ: Mapping[str, str] | None = None) -> int:
    """Judge one call and print the verdict document. Always exits 0.

    The verdict is the document, never the status: a channel reading a status
    can only learn the shapes it thought to implement.
    """
    env = environ if environ is not None else os.environ
    source = stdin if stdin is not None else sys.stdin
    try:
        request = json.loads(source.read())
        native_event = str(request["event"])
        payload = request["payload"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _say(_undeliverable(f"unreadable dispatch request: {exc}"))
    if not isinstance(payload, dict):
        return _say(_undeliverable("dispatch payload is not an object"))

    event = HookEvent.parse(native_event)
    if event is None:
        # Nothing composed can bind here, so no gate was missed.
        return _say(ChainVerdict(decision=ChainDecision.ALLOW))
    try:
        rows = _load_rows(env, event)
    except _ManifestError as exc:
        return _say(reduce_to(PROFILE.speaks, _undeliverable(str(exc), event)))

    return _say(
        reduce_to(
            PROFILE.speaks,
            run_chain(
                PROFILE,
                event=event,
                rows=rows,
                payloads=[_spoken(payload)],
                project_dir=project_dir_from(env),
                environ=env,
            ),
        )
    )


def _spoken(payload: dict) -> dict:
    """The call in the vocabulary hooks are written against, not OpenCode's."""
    native = str(payload.get("tool_name", ""))
    args = payload.get("tool_input")
    return {
        **payload,
        "tool_name": PROFILE.spoken_name(native) if native else "",
        "tool_input": speak_args(PROFILE, args if isinstance(args, dict) else {}),
    }


def _undeliverable(reason: str, event: HookEvent | None = None) -> ChainVerdict:
    """A gate set that never resolved, through the one constructor that also
    honours the hatch its refusal names."""
    return undeliverable(
        f"ai-hats-opencode-hook: {reason}",
        event=event,
        project_dir=project_dir_from(os.environ),
    )


def _say(verdict: ChainVerdict) -> int:
    # The wire document carries the verdict; the hooks' own stderr has no field
    # in it and goes to this process's stderr, which the plugin inherits.
    relay_stderr(verdict)
    sys.stdout.write(json.dumps(to_wire(verdict)) + "\n")
    return 0


def main() -> None:
    raise SystemExit(dispatch_hook())


if __name__ == "__main__":
    main()


__all__ = ["MANIFEST_VERSION", "dispatch_hook", "main"]
