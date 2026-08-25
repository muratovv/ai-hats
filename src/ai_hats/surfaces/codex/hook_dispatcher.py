"""Adapt Codex hook events to composed ai-hats (Claude-style) hook scripts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ai_hats.env import ENV_AI_HATS_DIR, ENV_SESSION_CACHE_DIR
from ai_hats_observe.trace import ENV_SESSION_ID
from .claude_hook_adapter import matches_claude_hook, to_claude_hook_payloads

DISPATCHER_COMMAND = (
    'sh -c \'if [ -n "$AI_HATS_SESSION_ID" ] && [ -n "$AI_HATS_DIR" ] '
    '&& [ -x "$AI_HATS_PYTHON" ]; then exec "$AI_HATS_PYTHON" '
    '-m ai_hats.surfaces.codex.hook_dispatcher; else printf "%s\\n" '
    '"ai-hats-codex-hook: incomplete dispatcher environment" >&2; exit 2; fi\''
)

HOOK_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class _HookResult:
    decision: str = ""
    reason: str = ""
    context: str = ""
    raw: dict | None = None


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


def _kill_group(running: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
    except OSError:
        running.kill()


def _run(command: str, payload: dict) -> _HookResult:
    try:
        with subprocess.Popen(  # noqa: S603 - manifest pins an executable inside skills_root
            [command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
            start_new_session=True,
        ) as running:
            try:
                stdout, stderr = running.communicate(
                    input=json.dumps(payload), timeout=HOOK_TIMEOUT_S
                )
            except subprocess.TimeoutExpired:
                _kill_group(running)
                return _HookResult("deny", f"hook timed out after {HOOK_TIMEOUT_S:g}s: {command}")
            code = running.returncode
    except (OSError, ValueError) as exc:
        return _HookResult("deny", f"hook could not start: {command}: {exc}")

    if code == 2:
        return _HookResult("deny", stderr.strip() or f"hook blocked: {command}")
    if code != 0:
        return _HookResult("deny", stderr.strip() or f"hook failed with exit {code}: {command}")
    if stderr:
        # Bypass journals and fail-open diagnostics deliberately use stderr;
        # swallowing it here would erase the audit trail while keeping the
        # underlying permission decision unchanged.
        sys.stderr.write(stderr)
    output = stdout.strip()
    if not output:
        return _HookResult()
    try:
        raw = json.loads(output)
    except ValueError:
        return _HookResult("deny", f"hook returned invalid JSON: {command}")
    if not isinstance(raw, dict):
        return _HookResult("deny", f"hook returned non-object JSON: {command}")
    hook_output = raw.get("hookSpecificOutput")
    hook_output = hook_output if isinstance(hook_output, dict) else {}
    decision = str(hook_output.get("permissionDecision", "")).lower()
    reason = str(hook_output.get("permissionDecisionReason", ""))
    if raw.get("decision") == "block":
        decision = "deny"
        reason = str(raw.get("reason", ""))
    return _HookResult(
        decision=decision,
        reason=reason,
        context=str(hook_output.get("additionalContext", "")),
        raw=raw,
    )


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
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    sys.stdout.write(json.dumps(output) + "\n")


def dispatch_hook(*, stdin=None) -> int:
    """Run the composed chain for the Codex event received on stdin."""
    source = stdin if stdin is not None else sys.stdin
    try:
        payload = json.loads(source.read())
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ai-hats-codex-hook: invalid payload: {exc}\n")
        return 2
    if not isinstance(payload, dict):
        sys.stderr.write("ai-hats-codex-hook: payload is not an object\n")
        return 2
    event = str(payload.get("hook_event_name", "PreToolUse"))
    if event not in ("PreToolUse", "PermissionRequest", "PostToolUse"):
        return 0
    try:
        manifest = _load_manifest(os.environ)
    except _ManifestError as exc:
        sys.stderr.write(f"ai-hats-codex-hook: {exc}\n")
        return 2

    source_event = "PreToolUse" if event == "PermissionRequest" else event
    raw_entries = manifest["hooks"].get(source_event, [])
    entries = raw_entries if isinstance(raw_entries, list) else []
    tool_name = str(payload.get("tool_name", ""))
    contexts: list[str] = []
    post_block: _HookResult | None = None
    for entry in entries:
        if not isinstance(entry, dict) or not matches_claude_hook(
            str(entry.get("matcher", "")), tool_name
        ):
            continue
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            _emit_deny(event, f"malformed hook entry: {entry.get('tag', '<untagged>')}")
            return 0
        for adapted in to_claude_hook_payloads(payload, event):
            result = _run(command, adapted)
            if result.decision == "deny":
                if event == "PostToolUse":
                    post_block = result
                    break
                _emit_deny(event, result.reason or f"blocked by {entry.get('tag', command)}")
                return 0
            # Codex cannot turn a PreToolUse `ask` into a native prompt. Deny
            # until the user supplies the hook's explicit ACK, while an ask
            # observed inside an existing PermissionRequest can safely defer to
            # that native prompt.
            if result.decision == "ask" and event == "PreToolUse":
                _emit_deny(
                    event,
                    result.reason
                    or "runtime policy requires explicit consent; set its ACK and retry",
                )
                return 0
            if result.context and result.context not in contexts:
                contexts.append(result.context)
        if post_block is not None:
            break

    if event == "PostToolUse" and post_block is not None:
        sys.stdout.write(json.dumps({"decision": "block", "reason": post_block.reason}) + "\n")
    elif contexts and event != "PermissionRequest":
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": "\n".join(contexts),
                    }
                }
            )
            + "\n"
        )
    return 0


def main() -> None:
    raise SystemExit(dispatch_hook())


if __name__ == "__main__":
    main()


__all__ = ["DISPATCHER_COMMAND", "dispatch_hook", "main"]
