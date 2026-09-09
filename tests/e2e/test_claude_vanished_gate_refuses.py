"""e2e (HATS-1868)

flow:   a composed gate's script is gone mid-session and the operator expects
        the tool call to be stopped, not waved through
cmds:
    sh -c "$DISPATCHER_COMMAND"   # the string a settings.json entry holds
expect: the call is refused, the refusal names the hatch that opens it, and the
        hatch actually opens it
why:    measured on claude 2.1.247 (poc-hook-delivery.md M4): with the script
        gone the harness ran the call anyway — rc=0, ZERO BYTES on stderr, the
        only trace a `hook_non_blocking_error` line inside the transcript JSONL.
        That is the harness's contract, not a bug, and it is unreachable from
        settings.json — HATS-1439 is what it cost
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.hook_chain import run_claude_dispatch
from _helpers.sessions import stand_in_session

pytestmark = [pytest.mark.guards, pytest.mark.surfaces]

SESSION_ID = "sid-claude-vanished"


def _mirror(cache: Path) -> Path:
    root = cache / "plugin" / "skills" / "safety-guard"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest(cache: Path, rows: list[dict]) -> None:
    (cache / "hooks.json").write_text(
        json.dumps({"version": 1, "session": {"id": SESSION_ID}, "hooks": {"PreToolUse": rows}}),
        encoding="utf-8",
    )


@pytest.fixture
def vanished(tmp_path: Path) -> SimpleNamespace:
    """A session whose manifest names a gate whose file is no longer there."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    mirror = _mirror(cache)
    _manifest(
        cache,
        [
            {
                "command": str(mirror / "VANISHED_GATE.py"),
                "matcher": "Bash",
                "tag": "ai-hats:safety-guard",
            }
        ],
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= {
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
    }
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    return SimpleNamespace(project=project, env=env, cache=cache, mirror=mirror)


def _spoken(done) -> dict:
    assert done.stdout.strip(), f"the dispatcher said nothing at all: {done.stderr!r}"
    return json.loads(done.stdout)["hookSpecificOutput"]


def test_a_gate_that_vanished_stops_the_call(vanished) -> None:
    done = run_claude_dispatch(
        vanished.project, vanished.env, tool="Bash", tool_input={"command": "echo hi"}
    )

    spoken = _spoken(done)
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert "AI_HATS_GATE_BROKEN_ACK" in spoken["permissionDecisionReason"], (
        f"a refusal ai-hats imposed must name the way past it:\n{done.stdout!r}"
    )


def test_the_hatch_the_refusal_names_actually_opens_it(vanished) -> None:
    """A deny naming a flag nobody reads is worth nothing (HATS-1253 P4), and
    on the MAIN surface it would mean a locked session with no way out."""
    done = run_claude_dispatch(
        vanished.project,
        vanished.env | {"AI_HATS_GATE_BROKEN_ACK": "1"},
        tool="Bash",
        tool_input={"command": "echo hi"},
    )

    assert "permissionDecision" not in done.stdout, done.stdout
    assert "SKIPPED" in done.stderr, (
        f"a skipped gate must not be skipped in silence:\n{done.stderr}"
    )


def test_the_control_a_live_gate_still_lets_the_call_through(vanished) -> None:
    """Without this, the refusal above is indistinguishable from a dispatcher
    that refuses every call it is handed."""
    live = vanished.mirror / "gate.sh"
    live.write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n", encoding="utf-8")
    live.chmod(0o755)
    _manifest(
        vanished.cache, [{"command": str(live), "matcher": "Bash", "tag": "ai-hats:safety-guard"}]
    )

    done = run_claude_dispatch(
        vanished.project, vanished.env, tool="Bash", tool_input={"command": "echo hi"}
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "", f"an allowing chain has nothing to say: {done.stdout!r}"


def test_a_gate_stripped_of_its_executable_bit_refuses_too(vanished) -> None:
    """Removing +x must not become a quiet way to disarm a gate."""
    inert = vanished.mirror / "inert.sh"
    inert.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    inert.chmod(0o644)
    _manifest(
        vanished.cache, [{"command": str(inert), "matcher": "Bash", "tag": "ai-hats:safety-guard"}]
    )

    done = run_claude_dispatch(
        vanished.project, vanished.env, tool="Bash", tool_input={"command": "echo hi"}
    )

    assert _spoken(done)["permissionDecision"] == "deny", done.stdout


def test_an_incomplete_dispatcher_environment_refuses_rather_than_passing(vanished) -> None:
    """The guard inside the settings.json string itself: without the session
    pins the dispatcher cannot even start, and starting is not optional."""
    blind = dict(vanished.env)
    blind.pop("AI_HATS_SESSION_CACHE_DIR")

    done = run_claude_dispatch(vanished.project, blind, tool="Bash", tool_input={"command": "hi"})

    assert done.returncode == 2, done.stdout
    assert "incomplete dispatcher environment" in done.stderr
    assert "AI_HATS_GATE_BROKEN_ACK" in done.stderr, (
        f"a refusal ai-hats imposes must name the way past it: {done.stderr!r}"
    )


def test_the_guard_honours_the_hatch_it_names(vanished) -> None:
    """The one refusal python never gets to honour: the guard lives in `sh` and
    the hatch it names must therefore be spelled there too. Reachable without
    any bug of ours — a venv rebuilt mid-session (`uv sync`, `wt create`) is
    enough to un-execute `AI_HATS_PYTHON` under a running session."""
    blind = dict(vanished.env) | {"AI_HATS_GATE_BROKEN_ACK": "1"}
    blind.pop("AI_HATS_SESSION_CACHE_DIR")

    done = run_claude_dispatch(vanished.project, blind, tool="Bash", tool_input={"command": "hi"})

    assert done.returncode == 0, done.stderr
    assert "SKIPPED" in done.stderr, f"a skipped gate must not be skipped in silence: {done.stderr}"
