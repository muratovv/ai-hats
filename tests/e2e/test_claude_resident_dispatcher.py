"""e2e (HATS-1874)

flow:   a composed claude session whose wrapper holds a dispatcher open, and the
        same session's settings entry run the way the harness runs it
cmds:
    sh -c "<the command settings.json holds>"
expect: the verdict is byte-identical to the one a spawned dispatcher gives, and
        it arrives even when no dispatcher COULD be spawned
why:    the resident path exists only to drop ~43 ms of interpreter and imports
        per tool call. The moment it answers differently from the spawn path it
        is a second gate implementation, which is the drift HATS-1858 spent a
        card removing
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from _helpers.sessions import stand_in_session

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.paths import session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.claude.hook_server import HookServer, socket_path
from ai_hats.surfaces.claude.provider import ClaudeSurface

SESSION_ID = "sid-resident"
DENY = '#!/bin/sh\ncat >/dev/null\nprintf "%s\\n" "the gate itself spoke" >&2\nexit 2\n'
ALLOW = "#!/bin/sh\ncat >/dev/null\nexit 0\n"


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
        project, result, SESSION_ID, run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= artifacts.extra_env | {"AI_HATS_PYTHON": sys.executable}
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    cache = session_cache_dir(project, SESSION_ID)
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
    assert resident == spawned


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
