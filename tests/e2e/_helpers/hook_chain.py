"""HATS-1253 — drive the MATERIALIZED PreToolUse chain, not a single hook.

Single-hook tests cannot observe hook B overriding hook A's verdict, which is
how a green test coexisted with the opposite live behaviour. This resolves the
Bash chain from the project's settings.json and returns the composite verdict.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CLAUDE_PROJECT_DIR_VAR = "$CLAUDE_PROJECT_DIR/"

#: Flags that grant consent. A deny whose text names none of these leaves the
#: agent nowhere to go — see the deny-names-its-hatch invariant (HATS-1253 P4).
ACK_FLAG_RE = re.compile(r"AI_HATS_[A-Z0-9_]*ACK")

#: Keys a surface can spell the Bash command line under. ``safety_gate.py``
#: answers in the key the surface spoke in — Claude Code says ``command``, agy
#: says ``CommandLine`` — so a runner that knows only one drops half the reply.
COMMAND_KEYS = ("command", "CommandLine")


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
    #: The tool input a hook rewrote, when it did — an `ask` carrying a consent
    #: ticket says both things in ONE reply, and dropping half of it here would
    #: report the question without its payload (HATS-1642).
    updated_input: dict | None = None

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


@dataclass(frozen=True)
class Approved:
    """What a real shell did with the command the supervisor approved.

    Deliberately dumb: no verdict verbs, no assertions of its own. The caller
    reads :attr:`returncode` — a helper that swallowed a non-zero exit would
    reproduce the blind spot it exists to remove (HATS-1682 T2).
    """

    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def __str__(self) -> str:  # pragma: no cover - assertion messages only
        return f"exit {self.returncode} from `{self.command}`\n{self.output.strip()[:800]}"


def approved_command(verdict: Verdict) -> str:
    """The command line ``verdict`` approved, in whichever key it answered.

    Raises on anything that is not an `ask` carrying a rewrite: running the
    ORIGINAL command there would silently test the ungated spelling, which is
    the failure mode this whole helper exists to end.
    """
    if verdict.decision != "ask":
        raise AssertionError(
            f"run_approved needs an `ask` to execute; the chain said {verdict}. "
            "Nothing was approved, so there is no approved command to run."
        )
    if not verdict.updated_input:
        raise AssertionError(
            f"the ask carried no updatedInput, so no command was approved: {verdict}"
        )
    for key in COMMAND_KEYS:
        value = verdict.updated_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise AssertionError(
        f"updatedInput names none of {COMMAND_KEYS}: {sorted(verdict.updated_input)}"
    )


def run_approved(
    project: Path,
    verdict: Verdict,
    *,
    env: dict | None = None,
    ack: str | None = None,
    timeout: int = 180,
) -> Approved:
    """Execute the command the chain approved, verbatim, through a real shell.

    Deliberate long contract — noqa: comment-length.

    The rewrite IS the delivery mechanism, so lifting the nonce out and handing
    it to a subprocess as an env var tests a path the product never takes — and
    reported a rewrite that exits 127 as a pass (HATS-1682 T2).

    ``bash -c``, not ``bash -lc``: the harness runs a Bash tool call in a
    non-login shell, and ``-l`` would source the developer's profile — PATH
    edits, exported ack flags — re-injecting the ambient state the harness does
    not have. :func:`_run_one` already runs the hooks that way, so the question
    and its answer meet the same shell.

    Ack hygiene matches :func:`run_tool_chain`: every ``AI_HATS_*ACK`` is
    stripped unless named in ``ack``, because an `ask` is only reachable when
    none was set. ``PATH`` leads with the interpreter's ``bin`` — a bare ``rack``
    would otherwise resolve to whatever checkout the developer's PATH names.
    """
    command = approved_command(verdict)

    base_env = dict(env) if env is not None else os.environ.copy()
    for key in [k for k in base_env if ACK_FLAG_RE.fullmatch(k)]:
        del base_env[key]
    base_env.pop("AI_HATS_YOLO", None)
    if ack:
        base_env[ack] = "1"
    if "AI_HATS_CONSENT_WRAPPER_CONFIG" not in base_env:
        bin_dir = str(Path(sys.executable).parent)
        base_env["PATH"] = os.pathsep.join([bin_dir, base_env.get("PATH", "")]).rstrip(os.pathsep)

    proc = subprocess.run(  # noqa: S603 - the command the guard itself wrote
        ["bash", "-c", command],  # noqa: S607 - bash from PATH, as the harness runs it
        cwd=str(project),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return Approved(command, proc.returncode, proc.stdout, proc.stderr)


def run_unasked(
    project: Path,
    command: str,
    *,
    env: dict | None = None,
    ack: str | None = None,
    timeout: int = 180,
) -> Approved:
    """Execute a command the chain did NOT turn into a question (HATS-1735).

    The grant road raises no `ask` and rewrites nothing, so :func:`run_approved`
    — which requires both — cannot run it. Same shell and the same ack hygiene
    on purpose: comparing "asked" against "not asked" is only a comparison when
    the two commands meet the same environment.

    Deliberately NOT verifying the verdict: a caller asserts that separately, and
    a runner that re-ran the chain would double every hook's side effects.
    """
    base_env = dict(env) if env is not None else os.environ.copy()
    for key in [k for k in base_env if ACK_FLAG_RE.fullmatch(k)]:
        del base_env[key]
    base_env.pop("AI_HATS_YOLO", None)
    if ack:
        base_env[ack] = "1"
    if "AI_HATS_CONSENT_WRAPPER_CONFIG" not in base_env:
        bin_dir = str(Path(sys.executable).parent)
        base_env["PATH"] = os.pathsep.join([bin_dir, base_env.get("PATH", "")]).rstrip(os.pathsep)

    proc = subprocess.run(  # noqa: S603 - the command under test, spelled by the caller
        ["bash", "-c", command],  # noqa: S607 - bash from PATH, as the harness runs it
        cwd=str(project),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return Approved(command, proc.returncode, proc.stdout, proc.stderr)


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
        rewritten = hso.get("updatedInput")
        rewritten = rewritten if isinstance(rewritten, dict) else None
        if decision in {"deny", "ask", "allow"}:
            # `allow` is a hook DECIDING, not the chain running out of objections:
            # `hook` names the decider, and stays empty for the default (HATS-1642).
            return Verdict(
                decision,
                str(hso.get("permissionDecisionReason", "")),
                name,
                context,
                rewritten,
            )
        if context or rewritten:
            return Verdict("allow", context=context, updated_input=rewritten)

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
    rewritten: dict | None = None
    allowed_by = ""
    for command_str in hooks:
        verdict = _run_one(command_str, payload, project, base_env)
        if verdict.context:
            contexts.append(verdict.context)
        if verdict.updated_input is not None:
            rewritten = verdict.updated_input
        if verdict.decision == "allow" and verdict.hook and not allowed_by:
            allowed_by = verdict.hook
        if verdict.gated:
            return Verdict(
                verdict.decision, verdict.reason, verdict.hook, "\n".join(contexts), rewritten
            )
    return Verdict("allow", hook=allowed_by, context="\n".join(contexts), updated_input=rewritten)


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


_FAKE_CLINE = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

if sys.argv[1:] == ["--version"]:
    print("3.0.3")
    raise SystemExit(0)

args = sys.argv[1:]
hooks_dir = Path(args[args.index("--hooks-dir") + 1])
manifest = json.loads((hooks_dir.parent / "hooks.json").read_text())
payload = {
    "hookName": "PreToolUse",
    "preToolUse": {
        "toolName": "run_commands",
        "parameters": {"commands": [os.environ["AI_HATS_CLINE_TEST_COMMAND"]]},
    },
    "workspaceRoots": [os.getcwd()],
}
hook = subprocess.run(
    [str(hooks_dir / "PreToolUse")],
    input=json.dumps(payload),
    env=os.environ.copy(),
    capture_output=True,
    text=True,
    timeout=30,
)
capture = {
    "argv": args,
    "session_id": os.environ["AI_HATS_SESSION_ID"],
    "manifest_session_id": manifest["session"]["id"],
    "pretooluse_tags": [
        entry.get("tag", "") for entry in manifest["hooks"].get("PreToolUse", [])
    ],
    "hook_returncode": hook.returncode,
    "hook_output": json.loads(hook.stdout),
    "hook_stderr": hook.stderr,
}
Path(os.environ["AI_HATS_CLINE_CAPTURE"]).write_text(json.dumps(capture))
"""


def install_cline_surface_venv(
    repo_src: Path,
    target: Path,
    env: dict[str, str],
    *,
    timeout: int = 300,
) -> Path:
    """Install the committed root and Cline surface into a private e2e venv."""
    uv = shutil.which("uv")
    if uv is None:
        raise AssertionError("uv is required for the Cline surface e2e")
    target.parent.mkdir(parents=True, exist_ok=True)
    created = subprocess.run(  # noqa: S603 - resolved uv executable
        [uv, "venv", "--python", sys.executable, str(target)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if created.returncode != 0:
        raise AssertionError(f"Cline e2e venv failed:\n{created.stdout}\n{created.stderr}")
    installed = subprocess.run(  # noqa: S603 - resolved uv executable and local package paths
        [
            uv,
            "pip",
            "install",
            "--python",
            str(target / "bin" / "python"),
            str(repo_src),
            str(repo_src / "packages" / "surfaces" / "cline"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if installed.returncode != 0:
        raise AssertionError(
            f"Cline e2e package install failed:\n{installed.stdout}\n{installed.stderr}"
        )
    return target


def run_cline_hook_session(
    launcher: Path,
    project: Path,
    env: dict[str, str],
    driver_dir: Path,
    *,
    command: str,
    timeout: int = 90,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Launch Cline HITL and drive its materialized composed PreToolUse chain."""
    fake_bin = driver_dir / "bin"
    fake_bin.mkdir(parents=True)
    fake_cline = fake_bin / "cline"
    fake_cline.write_text(_FAKE_CLINE)
    fake_cline.chmod(0o755)
    capture_path = driver_dir / "capture.json"
    launch_env = dict(env)
    launch_env["PATH"] = os.pathsep.join([str(fake_bin), launch_env.get("PATH", "")])
    launch_env["AI_HATS_CLINE_CAPTURE"] = str(capture_path)
    launch_env["AI_HATS_CLINE_TEST_COMMAND"] = command

    launched = subprocess.run(  # noqa: S603 - launcher is the installed test fixture
        [str(launcher), "-p", "cline", "-r", "maintainer"],
        cwd=project,
        env=launch_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    capture = json.loads(capture_path.read_text()) if capture_path.is_file() else {}
    return launched, capture
