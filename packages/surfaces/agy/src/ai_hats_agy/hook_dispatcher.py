"""Global Hook Dispatcher for AGY surface (HATS-1166).

Located entirely inside `ai_hats_agy` surface package.
Executes session-specific hooks from `<session_cache_dir>/hooks.json` when invoked
by the global AGY hook registered in `~/.gemini/antigravity-cli/settings.json`.

The cache dir arrives pre-resolved in `AI_HATS_SESSION_CACHE_DIR` (HATS-1398):
this process runs on every tool call, so it must not import ai-hats to re-derive
a path the session builder already knew.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import subprocess
from pathlib import Path

#: Per-hook budget. A runtime gate answers in milliseconds; past this, "slow" is
#: indistinguishable from "hung" and the tool call must not wait any longer.
HOOK_TIMEOUT_S = 60.0
_TIMEOUT_ENV = "AI_HATS_AGY_HOOK_TIMEOUT_S"

# Sanctioned mirror of the env contract (ADR-0025 D5): this process must not
# import ai-hats (see the module docstring), so the spellings are declared here
# and held against the home by ``tests/test_env_contract.py``.
ENV_SESSION_CACHE_DIR = "AI_HATS_SESSION_CACHE_DIR"
ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_AI_HATS_PROJECT_DIR = "AI_HATS_PROJECT_DIR"
ENV_SESSION_IDENTITY = "AI_HATS_SESSION_IDENTITY"


def _session_identity() -> dict | None:
    """This session, from the one envelope its launcher wrote (ADR-0025 D1).

    Read whole rather than reassembled from scalars: the scalars are projections
    of this value, and a reader taking them separately can be handed a pair that
    never belonged together. Parsed with stdlib ``json`` rather than
    ``SessionIdentity.from_env`` because this process runs on every tool call and
    must not import ai-hats (module docstring); the two are held equal by
    ``tests/test_env_contract.py``.
    """  # comment-length: allow — why this reader is hand-rolled is the contract
    raw = os.environ.get(ENV_SESSION_IDENTITY)
    if not raw:
        if os.environ.get(ENV_SESSION_ID):
            sys.stderr.write(
                "ai-hats-hook-dispatcher: session carries no AI_HATS_SESSION_IDENTITY "
                "— it predates HATS-1594 and its hooks are unreachable; restart it.\n"
            )
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        sys.stderr.write("ai-hats-hook-dispatcher: AI_HATS_SESSION_IDENTITY is not readable JSON\n")
        return None
    if not isinstance(data, dict):
        sys.stderr.write(
            f"ai-hats-hook-dispatcher: AI_HATS_SESSION_IDENTITY holds "
            f"{type(data).__name__}, not an object — hooks are unreachable\n"
        )
        return None
    return data


def _hook_timeout() -> float:
    """The effective budget: ``AI_HATS_AGY_HOOK_TIMEOUT_S`` or the default.

    Parsed here rather than reused from ``ai_hats.worktree_hooks``: this process
    runs on every tool call and must not import the integrator (module
    docstring). Anything unusable falls back — a typo must not disarm the bound.
    """
    raw = os.environ.get(_TIMEOUT_ENV)
    if not raw:
        return HOOK_TIMEOUT_S
    try:
        budget = float(raw)
    except ValueError:
        return HOOK_TIMEOUT_S
    return budget if budget > 0 else HOOK_TIMEOUT_S


def _kill_group(running: subprocess.Popen) -> None:
    """Kill the expired hook and whatever it started.

    Killing the shell alone leaves its children running against the very tool
    call the hook was gating.
    """
    try:
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
    except OSError:
        running.kill()


def _session_hooks_file() -> Path | None:
    """This session's hooks manifest, from the dir the builder pinned (HATS-1398).

    An ai-hats session without the pin predates the cache move; say so rather
    than exit 0, which reads exactly like "no hooks configured".
    """
    pinned = os.environ.get(ENV_SESSION_CACHE_DIR)
    if pinned:
        return Path(pinned) / "hooks.json"
    sys.stderr.write(
        "ai-hats-hook-dispatcher: AI_HATS_SESSION_CACHE_DIR unset — this session "
        "predates HATS-1398 and its hooks are unreachable; restart it.\n"
    )
    return None


def dispatch_hook(event_arg: str | None = None, tool_name: str | None = None) -> int:
    """Read session hooks manifest and execute matching hooks for this event."""
    identity = _session_identity()
    if not identity or not identity.get("id") or not identity.get("project_dir"):
        # Standalone agy run outside ai-hats session — no-op exit 0
        return 0

    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except (OSError, AttributeError):
        stdin_data = ""

    # Resolve event name from arg, stdin JSON payload, or fallback
    event = event_arg
    if not event:
        if stdin_data:
            try:
                payload = json.loads(stdin_data)
                if isinstance(payload, dict):
                    event = (
                        payload.get("hook_event_name")
                        or payload.get("event_name")
                        or payload.get("event")
                        or payload.get("hook")
                    )
            except (OSError, ValueError):
                pass
    if not event:
        event = "PreToolUse"

    hooks_file = _session_hooks_file()

    data: dict = {}
    if hooks_file and hooks_file.is_file():
        try:
            data = json.loads(hooks_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    event_hooks: list[dict] = []
    if isinstance(data, dict):
        raw_session_hooks = data.get(event, [])
        if isinstance(raw_session_hooks, list):
            event_hooks.extend(h for h in raw_session_hooks if isinstance(h, dict))
        elif isinstance(raw_session_hooks, dict):
            event_hooks.append(raw_session_hooks)

    user_hooks_file = Path.home() / ".gemini" / "config" / "hooks.json"
    if user_hooks_file.is_file():
        try:
            user_data = json.loads(user_hooks_file.read_text(encoding="utf-8"))
            if isinstance(user_data, dict):
                raw_user_hooks = user_data.get(event, [])
                if isinstance(raw_user_hooks, list):
                    event_hooks.extend(h for h in raw_user_hooks if isinstance(h, dict))
                elif isinstance(raw_user_hooks, dict):
                    event_hooks.append(raw_user_hooks)
        except (OSError, ValueError):
            pass

    for hook in event_hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        if not command and "hooks" in hook and isinstance(hook["hooks"], list):
            for inner in hook["hooks"]:
                if isinstance(inner, dict) and "command" in inner:
                    command = inner["command"]
                    break
        if not command or not isinstance(command, str):
            continue

        matcher = hook.get("matcher", "*")
        if tool_name and matcher != "*" and tool_name not in matcher.split("|"):
            continue

        budget = _hook_timeout()
        try:
            with subprocess.Popen(
                command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
                # Its own process group, so an expired hook dies whole.
                start_new_session=True,
            ) as running:
                try:
                    said, complained = running.communicate(input=stdin_data, timeout=budget)
                except subprocess.TimeoutExpired:
                    _kill_group(running)
                    # BROKE, not refuse (ADR-0020 D2): a killed hook never
                    # formed a verdict.
                    sys.stderr.write(
                        f"ai-hats-hook-dispatcher: hook broke: timed out "
                        f"after {budget:g}s: {command}\n"
                    )
                    return 1
                code = running.returncode

            if said:
                sys.stdout.write(said)
            if complained:
                sys.stderr.write(complained)

            if code != 0:
                return code
        except Exception as err:
            sys.stderr.write(f"ai-hats-hook-dispatcher error executing {command}: {err}\n")
            return 1

    return 0


def main() -> None:
    event_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    tool_name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AGY_TOOL_NAME")
    sys.exit(dispatch_hook(event_arg, tool_name))


if __name__ == "__main__":
    main()
