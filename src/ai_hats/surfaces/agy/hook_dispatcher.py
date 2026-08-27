"""Global Hook Dispatcher for AGY surface (HATS-1166).

Located entirely inside the `ai_hats.surfaces.agy` package.
Executes session-specific hooks from `<session_cache_dir>/hooks.json` when invoked
by the global AGY hook registered in `~/.gemini/antigravity-cli/settings.json`.

The cache dir arrives pre-resolved in `AI_HATS_SESSION_CACHE_DIR` (HATS-1398):
this process runs on every tool call, so it reads the pin rather than re-deriving
a path the session builder already knew.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from ai_hats.env import ENV_SESSION_CACHE_DIR
from ai_hats.session_identity import ENV_SESSION_IDENTITY
from ai_hats_observe.trace import ENV_SESSION_ID

from ..hook_channel import (
    ChainDecision,
    ChainVerdict,
    HookCall,
    HookEvent,
    HookRow,
    matches,
    project_dir_from,
    reduce_to,
    relay_stderr,
    resolve_hook_timeout,
    run_chain,
    undeliverable,
    worded,
)
from .claude_hook_adapter import (
    agy_tool_name,
    from_claude_decision,
    to_claude_payload,
)
from .profile import PROFILE


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


def _kill_group(running: subprocess.Popen) -> None:
    """Kill the expired hook and whatever it started.

    Killing the shell alone leaves its children running against the very tool
    call the hook was gating.
    """
    try:
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
    except OSError:
        running.kill()


class _ManifestError(RuntimeError):
    """The composed manifest did not resolve — no gate ran, and none can."""


def _session_hooks_file() -> Path:
    """This session's hooks manifest, from the dir the builder pinned (HATS-1398).

    Two ways to have none, and both are a delivery failure rather than an
    unguarded session: no pin at all, and a pin whose manifest is gone.
    """
    pinned = os.environ.get(ENV_SESSION_CACHE_DIR)
    if not pinned:
        raise _ManifestError(
            "AI_HATS_SESSION_CACHE_DIR unset — this session predates HATS-1398 "
            "and its hooks are unreachable; restart it"
        )

    hooks_file = Path(pinned) / "hooks.json"
    if not hooks_file.is_file():
        # The builder writes it with every pin, so absence is a reclaimed dir.
        raise _ManifestError(
            f"no hooks manifest at {hooks_file} — the builder writes one whenever it "
            f"pins the dir, so this session's hooks, safety guards included, have "
            f"stopped firing; restart it"
        )
    return hooks_file


def _answered(said: str, payload: dict) -> str:
    """A hook's verdict, in the dialect the surface speaks.

    Only a verdict that REWRITES the tool input needs turning back, and only
    that shape is touched: anything else — a plain decision, a non-JSON line a
    hook printed — is forwarded byte for byte rather than reformatted.
    """
    stripped = said.strip()
    if not stripped.startswith("{"):
        return said
    try:
        decision = json.loads(stripped)
    except ValueError:
        return said
    if not isinstance(decision, dict):
        return said
    answered = from_claude_decision(decision, payload)
    return said if answered is decision else json.dumps(answered) + "\n"


def _session_rows(event: HookEvent) -> list[HookRow]:
    """This session's composed rows, from the manifest the builder pinned.

    A row this cannot read is a gate that will not run, so it raises rather than
    skipping: dropping it silently is indistinguishable from never having
    declared it, which is the whole failure this channel removes.
    """
    hooks_file = _session_hooks_file()
    try:
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise _ManifestError(
            f"hooks manifest unusable at {hooks_file}: {err} — this session's hooks, "
            f"safety guards included, are not firing; restart it"
        ) from err
    raw = data.get(event.value, []) if isinstance(data, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise _ManifestError(f"hooks manifest at {hooks_file} lists no rows for {event.value}")
    rows: list[HookRow] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise _ManifestError(f"hook entry is {type(entry).__name__}, not an object")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            raise _ManifestError(f"malformed hook entry: {entry.get('tag', '<untagged>')}")
        rows.append(
            HookRow(
                command=Path(command),
                matcher=str(entry.get("matcher", "*")),
                tag=str(entry.get("tag") or command),
            )
        )
    return rows


def _user_hooks(event: str) -> list[dict]:
    """The user's OWN hooks, which are not an ai-hats composition.

    Kept off the execution primitive on purpose: these are arbitrary shell the
    user configured for their own agy, so ai-hats runs them the way agy would
    and claims no verdict of its own over them.

    ``event`` is agy's NATIVE name, not a bindable one: this file is keyed by
    what agy sends, and it has rows the composed channel cannot bind to.
    """
    user_file = Path.home() / ".gemini" / "config" / "hooks.json"
    if not user_file.is_file():
        return []
    try:
        data = json.loads(user_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        sys.stderr.write(f"ai-hats-hook-dispatcher: {user_file} is unreadable: {err}\n")
        return []
    raw = data.get(event, []) if isinstance(data, dict) else []
    if isinstance(raw, dict):
        raw = [raw]
    return [h for h in raw if isinstance(h, dict)] if isinstance(raw, list) else []


def _user_command(hook: dict) -> str:
    command = hook.get("command")
    if not command and isinstance(hook.get("hooks"), list):
        for inner in hook["hooks"]:
            if isinstance(inner, dict) and "command" in inner:
                command = inner["command"]
                break
    return command if isinstance(command, str) else ""


def _run_user_hooks(event: str, tool: str, spoken: str, payload: dict) -> int:
    """Run the user's own hooks, propagating whatever agy would have seen."""
    budget = resolve_hook_timeout()
    for hook in _user_hooks(event):
        command = _user_command(hook)
        if not command:
            continue
        if tool and not matches(PROFILE, str(hook.get("matcher", "*")), tool):
            continue
        try:
            with subprocess.Popen(  # noqa: S602 — the user's own shell line, by design
                command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
                start_new_session=True,
            ) as running:
                try:
                    said, complained = running.communicate(input=spoken, timeout=budget)
                except subprocess.TimeoutExpired:
                    _kill_group(running)
                    sys.stderr.write(
                        f"ai-hats-hook-dispatcher: user hook timed out "
                        f"after {budget:g}s: {command}\n"
                    )
                    return 1
                code = running.returncode
            if said:
                sys.stdout.write(_answered(said, payload))
            if complained:
                sys.stderr.write(complained)
            if code != 0:
                return code
        except OSError as err:
            sys.stderr.write(f"ai-hats-hook-dispatcher: cannot run {command}: {err}\n")
            return 1
    return 0


def _reply(verdict: ChainVerdict, payload: dict) -> int:
    """Say the verdict in the form agy acts on, whoever formed it.

    ``permissionDecision`` is that form, and it used to be reserved for a
    refusal a hook UTTERED, while a gate ai-hats could not deliver — the
    stronger class — left through the exit status alone. That is the weaker
    channel: a non-zero hook status is what agy reports and keeps going on
    (HATS-1439), so the imposed refusal was the one that could go unheeded.
    Both take the authoritative form now; the status still carries a hook's own
    exit code, because that one IS agy's protocol for the hook's own refusal.
    """  # comment-length: allow — which form binds on this surface is the contract
    relay_stderr(verdict)
    if verdict.decision is ChainDecision.ALLOW and not verdict.nudges:
        return 0
    spoken: dict = {"hookEventName": (verdict.event or HookEvent.PRE_TOOL_USE).value}
    if verdict.decision is not ChainDecision.ALLOW:
        spoken["permissionDecision"] = verdict.decision.value
        spoken["permissionDecisionReason"] = worded(verdict)
    if verdict.updated_input is not None:
        spoken["updatedInput"] = verdict.updated_input
    # Carried on every path: the dialect says this surface can hold them, and
    # dropping them on a refusal made that promise false for the hooks that ran
    # before the objector.
    if verdict.nudges:
        spoken["additionalContext"] = "\n".join(n.text for n in verdict.nudges)
    decision = from_claude_decision({"hookSpecificOutput": spoken}, payload)
    sys.stdout.write(json.dumps(decision) + "\n")
    if verdict.decision is ChainDecision.DENY:
        sys.stderr.write(worded(verdict) + "\n")
        return verdict.exit_code if verdict.exit_code not in (None, 0) else 0
    return 0


def dispatch_hook(
    event_arg: str | None = None,
    tool_name: str | None = None,
    stdin_data: str | None = None,
) -> int:
    """Read this session's hooks and run the ones this call matches.

    ``stdin_data`` is the surface's payload. A parameter rather than a read
    inside, so a caller can hand one over without a test having to patch the
    process's own stdin — the mock this seam exists to avoid.
    """
    identity = _session_identity()
    if not identity:
        # Standalone agy run outside an ai-hats session — nothing was composed,
        # so nothing is being skipped.
        return 0
    if not identity.get("id") or not identity.get("project_dir"):
        sys.stderr.write(
            "ai-hats-hook-dispatcher: AI_HATS_SESSION_IDENTITY names no session or no "
            "project — this session's hooks are unreachable; restart it.\n"
        )
        return 0

    if stdin_data is None:
        stdin_data = ""
        try:
            if not sys.stdin.isatty():
                stdin_data = sys.stdin.read()
        except (OSError, AttributeError):
            stdin_data = ""

    # The payload is the source of truth for what this call IS: the event, and
    # the tool it is about. argv carries both only when the surface chose to
    # pass them, and a silent argv used to switch the matcher filter off
    # entirely — every hook then ran on every call.
    payload: dict = {}
    if stdin_data:
        try:
            parsed = json.loads(stdin_data)
        except (OSError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed

    named = event_arg or (
        payload.get("hook_event_name")
        or payload.get("event_name")
        or payload.get("event")
        or payload.get("hook")
    )
    native = str(named or HookEvent.PRE_TOOL_USE.value)
    if native not in PROFILE.native_events:
        # An arrival this surface does not deliver. Nothing is bound to it on
        # either channel, so there is no gate to have missed.
        return 0

    # Translated ONCE, here. Before this the scripts translated themselves —
    # four of them hand-rolled, two not at all, and those two allowed whatever
    # they could not read.
    tool = agy_tool_name(payload) or (tool_name or "")
    spoken = json.dumps(to_claude_payload(payload, native)) if payload else stdin_data

    # The user's channel is keyed by the arrival agy sent; the composed one knows
    # only the two bindable events. Collapsing the first onto the second ran
    # every gate on a tool-less payload and read the wrong row of the user's file.
    event = HookEvent.parse(native)
    if event is None:
        return _run_user_hooks(native, tool, spoken, payload)

    try:
        rows = _session_rows(event)
    except _ManifestError as exc:
        # The user's own hooks do NOT run behind this: they gate a call that is
        # not going to happen.
        return _reply(
            undeliverable(
                f"ai-hats-hook-dispatcher: {exc}",
                event=event,
                project_dir=project_dir_from(os.environ),
            ),
            payload,
        )

    verdict = reduce_to(
        PROFILE.speaks,
        run_chain(
            PROFILE,
            event=event,
            rows=rows,
            # Always one call: an unreadable one must still meet its gates.
            calls=[HookCall(to_claude_payload(payload, native), tool)],
            project_dir=project_dir_from(os.environ),
        ),
    )
    code = _reply(verdict, payload)
    if code != 0 or verdict.decision is not ChainDecision.ALLOW:
        return code
    return _run_user_hooks(native, tool, spoken, payload)


def main() -> None:
    event_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    tool_name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AGY_TOOL_NAME")
    sys.exit(dispatch_hook(event_arg, tool_name))


if __name__ == "__main__":
    main()
