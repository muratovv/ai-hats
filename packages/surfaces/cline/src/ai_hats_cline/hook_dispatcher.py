"""Run composed ai-hats hooks for native Cline hook events."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from .claude_hook_adapter import matches_claude_hook, to_claude_hook_payloads

ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_AI_HATS_DIR = "AI_HATS_DIR"
ENV_SESSION_CACHE_DIR = "AI_HATS_SESSION_CACHE_DIR"
HOOK_TIMEOUT_S = 60.0


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


def _kill_group(running: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
    except OSError:
        running.kill()


def _run(command: str, payload: dict) -> dict | None:
    try:
        with subprocess.Popen(  # noqa: S603 - manifest pins an executable in skills_root
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
                sys.stderr.write(f"ai-hats-cline-hook: hook timed out: {command}\n")
                return None
            code = running.returncode
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ai-hats-cline-hook: hook could not start: {command}: {exc}\n")
        return None

    if stderr:
        sys.stderr.write(stderr)
    if code != 0:
        sys.stderr.write(f"ai-hats-cline-hook: hook exited {code}: {command}\n")
        return None
    output = stdout.strip()
    if not output:
        return None
    try:
        raw = json.loads(output)
    except ValueError as exc:
        sys.stderr.write(f"ai-hats-cline-hook: hook returned invalid JSON: {command}: {exc}\n")
        return None
    if not isinstance(raw, dict):
        sys.stderr.write(f"ai-hats-cline-hook: hook returned non-object JSON: {command}\n")
        return None
    return raw


def _emit(output: dict) -> None:
    sys.stdout.write(json.dumps(output) + "\n")


def dispatch_hook(event: str, *, stdin=None) -> int:
    """Run the composed chain for one Cline event and emit Cline-native JSON."""
    source = stdin if stdin is not None else sys.stdin
    try:
        payload = json.loads(source.read())
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ai-hats-cline-hook: invalid payload: {exc}\n")
        _emit({"cancel": False})
        return 0
    if not isinstance(payload, dict):
        sys.stderr.write("ai-hats-cline-hook: payload is not an object\n")
        _emit({"cancel": False})
        return 0
    if event not in {"PreToolUse", "PostToolUse"}:
        sys.stderr.write(f"ai-hats-cline-hook: unsupported event: {event}\n")
        _emit({"cancel": False})
        return 0
    try:
        manifest = _load_manifest(os.environ)
    except _ManifestError as exc:
        sys.stderr.write(f"ai-hats-cline-hook: {exc}\n")
        _emit({"cancel": False})
        return 0

    entries = manifest["hooks"].get(event, [])
    entries = entries if isinstance(entries, list) else []
    contexts: list[str] = []
    for adapted in to_claude_hook_payloads(payload, event):
        tool_name = str(adapted.get("tool_name", ""))
        for entry in entries:
            if not isinstance(entry, dict) or not matches_claude_hook(
                str(entry.get("matcher", "")), tool_name
            ):
                continue
            command = entry.get("command")
            if not isinstance(command, str) or not command:
                sys.stderr.write(
                    f"ai-hats-cline-hook: malformed hook entry: {entry.get('tag', '<untagged>')}\n"
                )
                continue
            raw = _run(command, adapted)
            if raw is None:
                continue
            hook_output = raw.get("hookSpecificOutput")
            hook_output = hook_output if isinstance(hook_output, dict) else {}
            decision = str(hook_output.get("permissionDecision", "")).lower()
            reason = str(hook_output.get("permissionDecisionReason", ""))
            context = str(hook_output.get("additionalContext", ""))
            if context and context not in contexts:
                contexts.append(context)
            if raw.get("decision") == "block":
                decision = "deny"
                reason = str(raw.get("reason", ""))
            if decision == "deny" and event == "PreToolUse":
                _emit(
                    {
                        "cancel": True,
                        "errorMessage": reason or f"blocked by {entry.get('tag', command)}",
                    }
                )
                return 0
            if decision == "ask" and event == "PreToolUse":
                prefix = reason or f"consent required by {entry.get('tag', command)}"
                _emit(
                    {
                        "cancel": True,
                        "errorMessage": (
                            f"{prefix}; Cline runtime hooks cannot ask for permission or "
                            "apply hook input rewrites; grant consent outside this tool "
                            "call and retry"
                        ),
                    }
                )
                return 0

    output: dict[str, object] = {"cancel": False}
    if contexts:
        output["contextModification"] = "\n".join(contexts)
    _emit(output)
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m ai_hats_cline.hook_dispatcher EVENT\n")
        raise SystemExit(2)
    raise SystemExit(dispatch_hook(sys.argv[1]))


if __name__ == "__main__":
    main()


__all__ = ["dispatch_hook", "main"]
