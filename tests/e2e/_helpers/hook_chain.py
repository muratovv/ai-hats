"""HATS-1253 — drive the MATERIALIZED PreToolUse chain, not a single hook.

Single-hook tests cannot observe hook B overriding hook A's verdict, which is
how a green test coexisted with the opposite live behaviour. This resolves the
Bash chain from the project's settings.json and returns the composite verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CLAUDE_PROJECT_DIR_VAR = "$CLAUDE_PROJECT_DIR/"

#: Flags that grant consent. A deny whose text names none of these leaves the
#: agent nowhere to go — see the deny-names-its-hatch invariant (HATS-1253 P4).
ACK_FLAG_RE = re.compile(r"AI_HATS_[A-Z0-9_]*ACK")


@dataclass(frozen=True)
class Verdict:
    """Composite outcome of the whole chain."""

    decision: str  # "allow" | "deny" | "ask"
    reason: str = ""
    hook: str = ""  # basename of the deciding hook; "" when allowed
    #: Non-gating ``additionalContext`` the chain fed back, joined across hooks.
    #: A nudge never touches ``decision``, so a test reading only the verdict
    #: cannot see what the agent was actually told (HATS-1630).
    context: str = ""

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def gated(self) -> bool:
        """The agent may not proceed on its own — the chain said deny or ask.

        Kept distinct from :attr:`denied` (HATS-1308): ``deny`` ends the call,
        ``ask`` hands the decision to the user. Asserting ``denied`` on an
        ``ask`` would misreport the guard just as badly as reading it as allow.
        """
        return self.decision in {"deny", "ask"}

    @property
    def names_ack_flag(self) -> bool:
        return bool(ACK_FLAG_RE.search(self.reason))

    def __str__(self) -> str:  # pragma: no cover - assertion messages only
        if not self.gated:
            return "allow"
        return f"{self.decision} by {self.hook}: {self.reason.strip()[:200]}"


def _matches_tool(matcher: str, tool: str) -> bool:
    """True when a settings.json matcher applies to ``tool``."""
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def build_session_settings(
    project: Path, role: str = "assistant", session_id: str = "sid-hooks"
) -> Path:
    """Compose a session and return its materialized settings.json.

    HATS-1170 moved hook wiring out of project-root ``.claude/settings.json``
    into the per-session cache, so the composed chain only exists once a
    session is built.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.paths import session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeProvider

    result = Assembler(project).composer.compose(role)
    ClaudeProvider().build_session_artifacts(
        project, result, session_id, run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )
    return session_cache_dir(project, session_id) / "settings.json"


def pretooluse_hooks(settings: Path, tool: str = "Bash") -> list[str]:
    """Hook commands wired onto ``tool`` in ``settings``, in recorded order.

    Returned verbatim, ``$CLAUDE_PROJECT_DIR`` unexpanded — the shell resolves
    it exactly as the harness does, so a wiring regression surfaces here.
    """
    if not settings.is_file():
        raise AssertionError(f"no materialized settings.json at {settings}")

    data = json.loads(settings.read_text())
    commands: list[str] = []
    for entry in data.get("hooks", {}).get("PreToolUse", []) or []:
        if not isinstance(entry, dict) or not _matches_tool(
            str(entry.get("matcher", "") or ""), tool
        ):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command"):
                commands.append(str(hook["command"]))
    return commands


def _run_one(command: str, payload: str, project: Path, env: dict) -> Verdict:
    # `bash -c <path>` execs the file so its shebang picks the interpreter —
    # running `bash <path>` would make bash parse a .py hook as shell.
    proc = subprocess.run(  # noqa: S603 - command comes from our own settings.json
        ["bash", "-c", command],  # noqa: S607 - bash from PATH, as the harness runs it
        input=payload,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    name = command.rsplit("/", 1)[-1]

    # Convention 1: exit 2 blocks, stderr carries the reason.
    if proc.returncode == 2:
        return Verdict("deny", proc.stderr, name)

    # Convention 2: a permissionDecision on stdout. `ask` is a gate too — it
    # routes the call to the user, so treating it as allow reports a guard that
    # escalated as one that waved the command through (HATS-1308).
    out = (proc.stdout or "").strip()
    if out:
        try:
            payload_out = json.loads(out)
        except json.JSONDecodeError:
            payload_out = {}
        hso = payload_out.get("hookSpecificOutput") or {}
        decision = str(hso.get("permissionDecision", "")).lower()
        context = str(hso.get("additionalContext", ""))
        if decision in {"deny", "ask"}:
            return Verdict(decision, str(hso.get("permissionDecisionReason", "")), name, context)
        if context:
            return Verdict("allow", context=context)

    return Verdict("allow")


def run_tool_chain(
    project: Path,
    tool: str,
    tool_input: dict,
    *,
    settings: Path,
    env: dict | None = None,
    ack: str | None = None,
) -> Verdict:
    """Run one ``tool`` call through the project's whole PreToolUse chain.

    ``ack`` names a consent flag to set to ``"1"`` for this run. Every known
    ack flag is stripped first, so an ambient one in the developer's shell can
    never make a test pass by accident.
    """
    base_env = dict(env) if env is not None else os.environ.copy()
    for key in [k for k in base_env if ACK_FLAG_RE.fullmatch(k)]:
        del base_env[key]
    base_env.pop("AI_HATS_YOLO", None)
    if ack:
        base_env[ack] = "1"

    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    )

    base_env.setdefault("CLAUDE_PROJECT_DIR", str(project))

    hooks = pretooluse_hooks(settings, tool)
    if not hooks:
        raise AssertionError(f"no {tool} PreToolUse hooks wired in {settings}")

    contexts: list[str] = []
    for command_str in hooks:
        verdict = _run_one(command_str, payload, project, base_env)
        if verdict.context:
            contexts.append(verdict.context)
        if verdict.gated:
            return Verdict(verdict.decision, verdict.reason, verdict.hook, "\n".join(contexts))
    return Verdict("allow", context="\n".join(contexts))


def run_chain(
    project: Path,
    command: str,
    *,
    settings: Path,
    env: dict | None = None,
    ack: str | None = None,
) -> Verdict:
    """Run a Bash ``command`` through the whole PreToolUse chain."""
    return run_tool_chain(
        project, "Bash", {"command": command}, settings=settings, env=env, ack=ack
    )


def run_agy_dispatch(
    project: Path,
    env: dict,
    *,
    event: str = "PreToolUse",
    tool: str = "Edit",
    tool_input: dict | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run one ``tool`` call through agy's whole hook chain, as agy runs it.

    agy composes on the far side of the boundary: one static command per event
    (``DISPATCHER_COMMAND``) runs the session manifest's hooks and then the
    user's, stopping at the first non-zero. So driving the composed chain here
    means running that one production string through a real shell — the exit
    code returned is the chain's verdict, not any single hook's.
    """
    from ai_hats_agy.global_hook import DISPATCHER_COMMAND

    payload = json.dumps(
        {"hook_event_name": event, "tool_name": tool, "tool_input": tool_input or {}}
    )
    return subprocess.run(  # noqa: S603 - the production dispatcher string, run as agy runs it
        ["sh", "-c", DISPATCHER_COMMAND, "sh", event, tool],  # noqa: S607 - sh from PATH
        input=payload,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
