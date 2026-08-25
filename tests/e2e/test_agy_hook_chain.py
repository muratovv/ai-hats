"""e2e (HATS-1776)

flow:   an agent on the agy surface running a shell command past the composed guards
cmds:
    python -m ai_hats.surfaces.agy.hook_dispatcher PreToolUse   # what agy's global hook runs
expect: the whole composed PreToolUse chain fires on agy's own tool and argument
        names, and refuses what it refuses on Claude
why: the chain was only ever driven on the Claude road, so two shipped guards —
     the shared-state backstop among them — were installed on agy and never
     invoked once, and nothing went red
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import git as _git

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_ID = "sid-agy-chain"

#: What the guards are for. Both are refused on the Claude road today; neither
#: was ever asked on this one.
FORCE_PUSH = "git push --force origin master"
DESTRUCTIVE = "rm -rf /"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "t@t.io")
    _git(project, "config", "user.name", "t")
    (project / "code.py").write_text("print('hi')\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def _session(project: Path, home: Path) -> Path:
    """Compose a role onto agy and build its session, the way launch does.

    ``HOME`` is redirected because the build registers agy's global dispatcher in
    the user's own settings — hermetic here, not a write into whoever runs this.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.paths import session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.agy.provider import AgyProvider

    result = Assembler(REPO_ROOT).composer.compose("maintainer")
    before = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        AgyProvider().build_session_artifacts(
            project, result, SESSION_ID, run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
        )
    finally:
        if before is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = before
    return session_cache_dir(project, SESSION_ID)


def _agy_call(command: str) -> str:
    """agy's own spelling of a terminal call: `toolCall.args.CommandLine`."""
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
            "cwd": ".",
        }
    )


def _dispatch(project: Path, cache: Path, payload: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env.update(
        {
            "AI_HATS_SESSION_ID": SESSION_ID,
            "AI_HATS_PROJECT_DIR": str(project),
            "AI_HATS_SESSION_CACHE_DIR": str(cache),
            "AI_HATS_SESSION_IDENTITY": json.dumps(
                {
                    "v": 1,
                    "id": SESSION_ID,
                    "role": "maintainer",
                    "provider": "agy",
                    "project_dir": str(project),
                    "session_dir": str(cache),
                    "skills_root": str(cache / "rules" / ".agents" / "skills"),
                }
            ),
        }
    )
    return subprocess.run(  # noqa: S603 - our own dispatcher, the way agy runs it
        [sys.executable, "-m", "ai_hats.surfaces.agy.hook_dispatcher", "PreToolUse"],
        input=payload,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _decisions(said: str) -> list[dict]:
    """Every verdict the chain printed, in order.

    Read as a STREAM of concatenated objects, not line by line: a hook is free
    to pretty-print, and several hooks answer one after another on one stdout.
    """
    decoder = json.JSONDecoder()
    out: list[dict] = []
    rest = said.strip()
    while rest.startswith("{"):
        try:
            parsed, end = decoder.raw_decode(rest)
        except ValueError:
            break
        inner = parsed.get("hookSpecificOutput") if isinstance(parsed, dict) else None
        if isinstance(inner, dict):
            out.append(inner)
        rest = rest[end:].strip()
    return out


@pytest.fixture
def agy_chain(tmp_path: Path):
    project = _project(tmp_path)
    cache = _session(project, tmp_path / "home")
    return lambda command: _dispatch(project, cache, _agy_call(command))


def test_the_manifest_carries_the_bash_guards_at_all(tmp_path: Path):
    """Before the chain: the rows must BE there. They always were — that is what
    made the miss invisible, since every report showed them installed."""
    cache = _session(_project(tmp_path), tmp_path / "home")

    manifest = json.loads((cache / "hooks.json").read_text())
    commands = [h.get("command", "") for h in manifest.get("PreToolUse", [])]

    assert any("pre_bash_shared_state_guard.sh" in c for c in commands), commands
    assert any("safety_gate.py" in c for c in commands), commands


def test_a_destructive_command_is_refused_on_agys_own_names(agy_chain):
    """The chain, driven end to end on agy's tool and argument spelling."""
    res = agy_chain(DESTRUCTIVE)

    verdicts = _decisions(res.stdout)
    assert any(v.get("permissionDecision") == "deny" for v in verdicts), res.stdout or res.stderr


def test_the_shared_state_backstop_reaches_a_force_push_here_too(agy_chain):
    """The guard `rule_pause_before_shared_state_write` names as its backstop.
    Its matcher says `Bash`; agy calls the tool `run_command`; until the bridge
    the dispatcher compared those two literally and skipped the row."""
    res = agy_chain(FORCE_PUSH)

    verdicts = _decisions(res.stdout)
    gated = [v for v in verdicts if v.get("permissionDecision") in {"deny", "ask"}]
    assert gated, f"a force-push passed the whole chain: {res.stdout or res.stderr}"


def test_an_ordinary_command_is_not_gated(agy_chain):
    """The other half of a guard being armed: it must not fire on everything.
    A dispatcher that ran every hook on every call would pass the tests above
    while denying `ls`."""
    res = agy_chain("ls -la")

    denied = [v for v in _decisions(res.stdout) if v.get("permissionDecision") == "deny"]
    assert not denied, res.stdout
