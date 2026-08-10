"""e2e (HATS-1252, HATS-1373)

flow:   an agent triggering tool execution with unparsable or malformed hook payloads
cmds:
    # agent invoking a PreToolUse hook with an unparsable payload
    bash .agent/ai-hats/library/hooks/safety_gate.py < /tmp/malformed.json
expect: execution passes fail-open without blocking the call and the unparsable payload
        event is recorded in the bypass journal or stderr
why:    a hook that fails on unreadable payload must allow the call while leaving an
        audit trace so dead or broken hooks do not silently mask failures
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import (  # noqa: E402
    build_session_settings,
    pretooluse_hooks,
    run_chain,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GARBAGE_PAYLOAD = "this is not json at all {{{"


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash hook chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("fail-open-home"))

    project = tmp_path_factory.mktemp("fail-open-proj")
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    return project, env, build_session_settings(project)


def _run_with_stdin(command: str, project: Path, env: dict, payload: str):
    """One hook, one payload — the chain helper's runner without its JSON framing.

    ``CLAUDE_PROJECT_DIR`` is what the settings.json command interpolates to
    reach the hook file; without it bash resolves a bare ``/.agent/...`` and
    every hook "fails" with exit 127 instead of running.
    """
    run_env = dict(env)
    run_env.setdefault("CLAUDE_PROJECT_DIR", str(project))
    return subprocess.run(  # noqa: S603 - command comes from our own settings.json
        ["bash", "-c", command],  # noqa: S607 - bash from PATH, as the harness runs it
        input=payload,
        cwd=str(project),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.integration
def test_the_chain_is_actually_wired(hooked_project):
    """Green must mean 'checked and clean', never 'matched nothing'."""
    _project, _env, settings = hooked_project

    assert pretooluse_hooks(settings, "Bash"), f"no Bash PreToolUse hooks in {settings}"


@pytest.mark.integration
def test_an_unparsable_payload_fails_open_across_the_whole_chain(hooked_project):
    """Fail-open is the contract: an unreadable payload must never block a call."""
    project, env, settings = hooked_project

    for command in pretooluse_hooks(settings, "Bash"):
        proc = _run_with_stdin(command, project, env, GARBAGE_PAYLOAD)

        assert proc.returncode == 0, (
            f"{command.rsplit('/', 1)[-1]} blocked the call on an unparsable "
            f"payload (exit {proc.returncode}); fail-open is the contract.\n{proc.stderr}"
        )


#: tool -> the python hook on that matcher whose payload guard HATS-1373 changed.
#: Named per tool rather than scanned, so a hook silently dropping off its
#: matcher fails here instead of vacuously passing. (py_security_lint and
#: comment_length_lint carry the same change on PostToolUse, a different event.)
GUARDED = {
    "Bash": "safety_gate.py",
    "Write": "wt_gate.py",
    "EnterWorktree": "wt_entry_gate.py",
}


@pytest.mark.integration
@pytest.mark.parametrize(("tool", "hook_name"), sorted(GUARDED.items()))
def test_every_guarded_hook_records_its_fail_open(hooked_project, tool, hook_name):
    """Fail-under-revert: drop that hook's journal_bypass call and this goes quiet.

    The record lands in the bypass journal when the project is a git repo and on
    stderr when it is not — the helper says so loudly rather than dropping it.
    Either channel satisfies the contract; silence does not.
    """
    project, env, settings = hooked_project
    journal = project / ".git" / "ai-hats" / "bypasses.jsonl"

    matching = [c for c in pretooluse_hooks(settings, tool) if c.endswith(hook_name)]
    assert matching, f"{hook_name} is not wired on the {tool} matcher: {settings}"

    for command in matching:
        before = journal.read_text() if journal.exists() else ""
        proc = _run_with_stdin(command, project, env, GARBAGE_PAYLOAD)
        appended = (journal.read_text() if journal.exists() else "")[len(before) :]

        assert "fail-open" in proc.stderr or "fail-open" in appended, (
            f"{hook_name} waved a {tool} call through on an unparsable payload "
            f"without recording it — the silence HATS-1373 removed is back.\n"
            f"stderr={proc.stderr!r} journal+={appended!r}"
        )


@pytest.mark.integration
def test_reporting_the_fail_open_did_not_soften_a_real_deny(hooked_project):
    """The guards still guard: adding a journal call must not open a hole."""
    project, env, settings = hooked_project

    verdict = run_chain(project, "rm -rf /", settings=settings, env=env)

    assert verdict.denied, f"`rm -rf /` must still be denied by the chain; got {verdict}"


@pytest.mark.integration
def test_a_well_formed_benign_call_still_passes(hooked_project):
    """The counter-test: the chain has not become a blanket deny either."""
    project, env, settings = hooked_project

    verdict = run_chain(project, "echo hello", settings=settings, env=env)

    assert not verdict.denied, f"a benign command must pass the chain; got {verdict}"


@pytest.mark.integration
def test_a_valid_payload_is_still_parsed(hooked_project):
    """Guards the assertion above from passing for the wrong reason."""
    project, env, settings = hooked_project
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
    )

    for command in pretooluse_hooks(settings, "Bash"):
        proc = _run_with_stdin(command, project, env, payload)

        assert proc.returncode == 0, f"{command} rejected a valid payload:\n{proc.stderr}"
        assert "fail-open" not in proc.stderr, (
            f"{command} reported a fail-open for a payload it should have parsed:\n{proc.stderr}"
        )
