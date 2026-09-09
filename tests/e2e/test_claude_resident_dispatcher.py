"""e2e (HATS-1874)

flow:   a composed claude session whose wrapper holds a dispatcher open, and the
        same session's settings entry run the way the harness runs it
cmds:
    sh -c "<the command settings.json holds>"
expect: the verdict is byte-identical to the one a spawned dispatcher gives, and
        it arrives even when no dispatcher COULD be spawned; the gates it runs
        inherit the SESSION's environment, so the consent gate asks through it
        with its ticket and refuses the spelling that skips the wrapper
why:    the resident path exists only to drop ~43 ms of interpreter and imports
        per tool call. The moment it answers differently from the spawn path it
        is a second gate implementation, which is the drift HATS-1858 spent a
        card removing — and for four days it was one: the server lives in the
        wrapper process, whose environment is not the session's, so every gate
        reading the envelope saw no session and the consent question never
        reached the human
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import os
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from _helpers.git import init_repo
from _helpers.sessions import stand_in_session

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.claude.hook_server import HookServer, socket_path
from ai_hats.surfaces.claude.provider import ClaudeSurface

pytestmark = [pytest.mark.guards, pytest.mark.surfaces]

SESSION_ID = "sid-resident"
DENY = '#!/bin/sh\ncat >/dev/null\nprintf "%s\\n" "the gate itself spoke" >&2\nexit 2\n'
ALLOW = "#!/bin/sh\ncat >/dev/null\nexit 0\n"
#: A gate whose refusal names the session it ran in — or `unset`.
ECHO = (
    "#!/bin/sh\ncat >/dev/null\n"
    'printf \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
    '"permissionDecision":"deny","permissionDecisionReason":"session=%s"}}\\n\' '
    '"${AI_HATS_SESSION_ID-unset}"\n'
)
#: What the session's envelope is spelled as; the wrapper process has none of it.
ENVELOPE = ("AI_HATS_SESSION_ID", "AI_HATS_SESSION_IDENTITY", "AI_HATS_SESSION_CACHE_DIR")
ACK_FLAG = re.compile(r"AI_HATS_[A-Z0-9_]*ACK")


def _session(tmp_path: Path, body: str = DENY):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    skill_dir = tmp_path / "skills" / "gatekeeper"
    (skill_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: gatekeeper\nai_hats:\n  runtime_hooks:\n    PreToolUse:\n"
        "      - matcher: Bash\n        script: hooks/gate.sh\n---\n# Gatekeeper\n",
        encoding="utf-8",
    )
    gate = skill_dir / "hooks" / "gate.sh"
    gate.write_text(body)
    gate.chmod(0o755)

    result = CompositionResult(
        name="r",
        priorities=[],
        rules=[],
        skills=[
            ResolvedComponent(
                name="gatekeeper", component_type=ComponentKind.SKILL, source_path=skill_dir
            )
        ],
        injections=[],
    )
    artifacts = ClaudeSurface().build_session_artifacts(
        ProjectLayout.at(project),
        result,
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= artifacts.extra_env | {"AI_HATS_PYTHON": sys.executable}
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    cache = ProjectLayout.at(project).cache.session(SESSION_ID)
    entry = json.loads((cache / "settings.json").read_text())["hooks"]["PreToolUse"][0]
    return project, env, cache, entry["hooks"][0]["command"]


def _call(entry: str, project: Path, env: dict, command: str = "echo hi"):
    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    )
    done = subprocess.run(  # noqa: S602 - the settings.json string, as the harness runs it
        entry,
        shell=True,
        input=payload,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.returncode, done.stdout, done.stderr


@pytest.mark.integration
def test_the_resident_answer_is_the_spawned_one_byte_for_byte(tmp_path: Path) -> None:
    project, env, cache, entry = _session(tmp_path)

    spawned = _call(entry, project, env)
    with HookServer(cache, env):
        resident = _call(entry, project, env)

    assert "the gate itself spoke" in spawned[2], f"the control never ran the gate: {spawned}"
    assert resident == spawned, "HATS-1874: the resident answer is not a second implementation"


@pytest.mark.integration
def test_the_resident_answers_where_nothing_could_be_spawned(tmp_path: Path) -> None:
    """Positive control for every timing claim about this path: with no usable
    interpreter the spawn branch can only refuse, so a verdict proves the socket
    carried it."""
    project, env, cache, entry = _session(tmp_path)
    blind = dict(env) | {"AI_HATS_PYTHON": "/nonexistent/python"}

    assert _call(entry, project, blind)[0] == 2, "the spawn branch was not actually disabled"

    with HookServer(cache, blind):
        status, out, err = _call(entry, project, blind)

    assert "the gate itself spoke" in err, err
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny", out


@pytest.mark.integration
def test_a_socket_left_by_a_dead_session_falls_back_rather_than_refusing(tmp_path: Path) -> None:
    """A file where a socket used to be must not become a locked session."""
    project, env, cache, entry = _session(tmp_path)
    server = HookServer(cache, env).start()
    server._server.server_close()  # the process is gone; its socket file is not

    status, out, err = _call(entry, project, env)

    assert "the gate itself spoke" in err, f"the spawn fallback did not run: {err!r}"
    server.close()


@pytest.mark.integration
def test_two_calls_at_once_do_not_share_a_verdict(tmp_path: Path) -> None:
    """One process now answers several calls: the verdict is written to
    `sys.stdout`, which is process-global — so this is the test that a thread's
    answer stays its own."""
    project, env, cache, entry = _session(tmp_path)

    with HookServer(cache, env):
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _: _call(entry, project, env), range(4)))

    for status, out, err in answers:
        assert out.count("hookSpecificOutput") == 1, (
            f"this call's verdict is not exactly its own: {out!r}"
        )
        assert err.count("the gate itself spoke") == 2, f"stderr crossed threads: {err!r}"


@pytest.mark.integration
def test_the_socket_is_private_and_leaves_nothing_behind(tmp_path: Path) -> None:
    project, env, cache, _ = _session(tmp_path)
    path = socket_path(cache)

    with HookServer(cache, env):
        assert path.exists()
        assert oct(path.stat().st_mode)[-3:] == "600"
        assert oct(path.parent.stat().st_mode)[-3:] == "700"

    assert not path.exists(), "a socket outlived the session that owned it"


@pytest.mark.integration
def test_a_request_that_never_finishes_does_not_wedge_the_session(tmp_path: Path) -> None:
    """The server lives inside the process that owns the terminal, so a client
    that connects and says nothing must be dropped rather than held — and the
    drop must not print a traceback into that terminal."""
    project, env, cache, _ = _session(tmp_path)

    with HookServer(cache, env) as server:
        server._server.RequestHandlerClass.timeout = 0.2
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as mute:
            mute.connect(str(server.path))
            mute.settimeout(5)
            assert mute.recv(4096) == b"", "a silent client was answered anyway"

        status, out, err = _call(_session_entry(cache), project, env)

    assert "the gate itself spoke" in err, "the server stopped answering after a mute client"


def _session_entry(cache: Path) -> str:
    return json.loads((cache / "settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0][
        "command"
    ]


# ----- the gates run in the session's environment, not the wrapper's ---------


def _not_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """This process stands in for the wrapper that holds the server: outside a
    session. Without this the run inherits whatever session launched pytest,
    and the red the test exists for is invisible from inside one."""
    for name in ENVELOPE:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.integration
def test_the_resident_gates_inherit_the_sessions_environment(tmp_path: Path, monkeypatch) -> None:
    _not_the_session(monkeypatch)
    project, env, cache, entry = _session(tmp_path, body=ECHO)

    spawned = _call(entry, project, env)
    with HookServer(cache, env):
        resident = _call(entry, project, env)

    assert f"session={SESSION_ID}" in spawned[1], (
        f"control: the spawn path saw no session: {spawned}"
    )
    assert resident == spawned, "the resident path ran the gate outside the session"


def _composed_session(tmp_path: Path):
    """The shipped `assistant` role, gates and consent declaration included, in a
    git project — what a real launch composes, minus the harness."""
    from ai_hats.assembler import Assembler

    project = tmp_path / "project"
    project.mkdir()
    init_repo(project, branch="master")
    (project / "ai-hats.yaml").write_text("task_prefix: SBX\n", encoding="utf-8")
    (project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks").mkdir(parents=True)

    result = Assembler(project).composer.compose("assistant")
    artifacts = ClaudeSurface().build_session_artifacts(
        ProjectLayout.at(project),
        result,
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= artifacts.extra_env | {"AI_HATS_PYTHON": sys.executable}
    # An `ask` is only reachable when nothing pre-approved the move.
    for name in [key for key in env if ACK_FLAG.fullmatch(key)]:
        del env[name]
    for name in ("AI_HATS_YOLO", "AI_HATS_CONSENT_TICKET"):
        env.pop(name, None)
    cache = ProjectLayout.at(project).cache.session(SESSION_ID)
    return project, env, cache, _session_entry(cache)


def _verdict(answer: tuple[int, str, str]) -> dict:
    status, out, err = answer
    assert out.strip(), f"no verdict document came back (exit {status}): {err!r}"
    return json.loads(out)["hookSpecificOutput"]


@pytest.mark.integration
def test_the_consent_gate_asks_through_the_resident_dispatcher(tmp_path: Path, monkeypatch) -> None:
    """The gate that measured this: `safety_gate.py` reads the role's consent
    declaration out of the session envelope, and read "outside a session" from
    the wrapper's environment — asking nothing, journaling nothing, on every
    move the role declared. The wrapper spelling that skips the middleware was
    open the same way."""
    _not_the_session(monkeypatch)
    project, env, cache, entry = _composed_session(tmp_path)
    move = "rack transition SBX-001 execute"
    around = "/opt/elsewhere/rack transition SBX-001 done"

    spawned_move, spawned_around = (_call(entry, project, env, c) for c in (move, around))
    with HookServer(cache, env):
        resident_move, resident_around = (_call(entry, project, env, c) for c in (move, around))
        resident_rm = _call(entry, project, env, "rm -rf /")

    # The control: the spawned dispatcher, inside the session, asks and refuses.
    assert _verdict(spawned_move)["permissionDecision"] == "ask", spawned_move
    assert _verdict(spawned_around)["permissionDecision"] == "deny", spawned_around

    asked = _verdict(resident_move)
    assert asked["permissionDecision"] == "ask", resident_move
    assert asked["updatedInput"]["command"].startswith("AI_HATS_CONSENT_TICKET="), asked
    refused = _verdict(resident_around)
    assert refused["permissionDecision"] == "deny", resident_around
    assert "protected by this role" in refused["permissionDecisionReason"], refused
    # A gate that needs no envelope answered all along; this pins that it still does.
    assert _verdict(resident_rm)["permissionDecision"] == "deny", resident_rm
