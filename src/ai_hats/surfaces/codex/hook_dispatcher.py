"""Adapt Codex hook events to composed ai-hats (Claude-style) hook scripts."""

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
    HookEvent,
    HookRow,
    project_dir_from,
    reduce_to,
    relay_stderr,
    run_chain,
    undeliverable,
    worded,
)
from .claude_hook_adapter import to_claude_hook_payloads
from .profile import PROFILE

DISPATCHER_COMMAND = (
    'sh -c \'if [ -n "$AI_HATS_SESSION_ID" ] && [ -n "$AI_HATS_DIR" ] '
    '&& [ -x "$AI_HATS_PYTHON" ]; then exec "$AI_HATS_PYTHON" '
    '-m ai_hats.surfaces.codex.hook_dispatcher; else printf "%s\\n" '
    '"ai-hats-codex-hook: incomplete dispatcher environment" >&2; exit 2; fi\''
)


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
            "incomplete ai-hats hook identity: AI_HATS_SESSION_ID, AI_HATS_DIR, "
            "and AI_HATS_SESSION_CACHE_DIR are all required"
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
    manifest_id = identity.get("id")
    manifest_hats_dir = identity.get("ai_hats_dir")
    if manifest_id != session_id or not isinstance(manifest_hats_dir, str):
        raise _ManifestError(
            f"session identity mismatch: env={session_id!r}, manifest={manifest_id!r}"
        )
    if _resolved(manifest_hats_dir) != _resolved(hats_dir):
        raise _ManifestError(
            "session identity mismatch: manifest AI_HATS_DIR does not match the launcher"
        )
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise _ManifestError(f"hook manifest at {path} carries no hooks mapping")
    declared_skills_root = identity.get("skills_root")
    if declared_skills_root is None:
        skills_root = (_resolved(cache_dir) / "codex-home" / "skills").resolve()
    elif not isinstance(declared_skills_root, str) or not Path(declared_skills_root).is_absolute():
        raise _ManifestError(f"hook manifest at {path} carries an invalid skills root")
    else:
        skills_root = _resolved(declared_skills_root)
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


def _emit_deny(event: str, reason: str) -> None:
    if event == "PermissionRequest":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": reason},
            }
        }
    else:
        output = {
            "hookSpecificOutput": {
                "hookEventName": HookEvent.PRE_TOOL_USE.value,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
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


def _emit(verdict, native_event: str) -> int:
    relay_stderr(verdict)
    if verdict.decision is ChainDecision.ASK:
        # Codex has a prompt only where it was already asking. Elsewhere the
        # question has nowhere to go, and a question nobody sees is an allow.
        if native_event == "PermissionRequest":
            return 0
        _emit_deny(native_event, worded(verdict))
        return 0
    if verdict.decision is ChainDecision.DENY:
        if native_event == "PostToolUse":
            sys.stdout.write(json.dumps({"decision": "block", "reason": worded(verdict)}) + "\n")
        else:
            _emit_deny(native_event, worded(verdict))
        return 0
    if verdict.nudges and native_event != "PermissionRequest":
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": native_event,
                        "additionalContext": "\n".join(n.text for n in verdict.nudges),
                    }
                }
            )
            + "\n"
        )
    return 0


def _undeliverable(reason: str, native_event: str, event: HookEvent | None) -> int:
    """A gate set that never resolved, said in codex's own dialect.

    Through the channel rather than past it: a bare status carried no verdict
    codex could act on and, more to the point, named no way out — the one thing
    every delivery refusal on this channel owes the human.
    """
    verdict = undeliverable(
        f"ai-hats-codex-hook: {reason}",
        event=event,
        project_dir=project_dir_from(os.environ),
    )
    _emit(verdict, native_event)
    if verdict.decision is ChainDecision.DENY:
        # Also on stderr: codex shows the model the JSON, an operator reading
        # the session log afterwards reads this.
        sys.stderr.write(worded(verdict) + "\n")
        # The status stays codex's own refusal protocol; what it never carried
        # is the named way past, which the JSON above now does.
        return 2
    return 0


def dispatch_hook(*, stdin=None) -> int:
    """Run the composed chain for the Codex event received on stdin."""
    source = stdin if stdin is not None else sys.stdin
    pre = HookEvent.PRE_TOOL_USE
    try:
        payload = json.loads(source.read())
    except (OSError, ValueError) as exc:
        return _undeliverable(f"invalid payload: {exc}", pre.value, pre)
    if not isinstance(payload, dict):
        return _undeliverable("payload is not an object", pre.value, pre)
    native_event = str(payload.get("hook_event_name", HookEvent.PRE_TOOL_USE.value))
    if native_event not in PROFILE.native_events:
        return 0
    # A PermissionRequest is Codex's own arrival for a call the chain judges as
    # a PreToolUse. The mapping stops here, so the channel never learns a name
    # only this surface uses.
    event = HookEvent.parse(native_event) or HookEvent.PRE_TOOL_USE
    try:
        rows = _rows(_load_manifest(os.environ), event)
    except _ManifestError as exc:
        return _undeliverable(str(exc), native_event, event)

    verdict = reduce_to(
        PROFILE.speaks,
        run_chain(
            PROFILE,
            event=event,
            rows=rows,
            payloads=to_claude_hook_payloads(payload, native_event),
            project_dir=project_dir_from(os.environ),
        ),
    )
    return _emit(verdict, native_event)


def main() -> None:
    raise SystemExit(dispatch_hook())


if __name__ == "__main__":
    main()


__all__ = ["DISPATCHER_COMMAND", "dispatch_hook", "main"]
