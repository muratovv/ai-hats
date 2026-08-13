"""e2e (HATS-1630)

flow:   an agent running a raw grep/find in a session whose tool set may not
        include the dedicated Grep/Glob tools
cmds:
    grep -rn PATTERN src/
    find . -name '*.py'
expect: the hygiene nudge conditions its advice on the tool being available and
        still never gates the call
why:    the PreToolUse payload carries no tool inventory, so a nudge that names
        a tool outright can advise one that does not exist — and a Grep-less
        session must keep its only search path open
"""

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import build_session_settings, run_chain


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash hook chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("hygiene-nudge-home"))

    project = tmp_path_factory.mktemp("hygiene-nudge-proj")
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


@pytest.mark.integration
def test_grep_nudge_conditions_on_availability_and_never_gates(hooked_project):
    project, env, settings = hooked_project

    verdict = run_chain(project, "grep -rn PATTERN src/", settings=settings, env=env)

    assert verdict.decision == "allow", f"the hygiene nudge must never gate: {verdict}"
    assert verdict.context, "no additionalContext — the chain did not nudge at all"
    assert "if the Grep tool is available" in verdict.context, verdict.context
    # Pre-HATS-1630 wording asserted the tool exists; it must be gone.
    assert "prefer the Grep tool (" not in verdict.context, verdict.context


@pytest.mark.integration
def test_grep_nudge_offers_a_followable_fallback(hooked_project):
    """A Grep-less session needs to be told what to do, not only what to prefer."""
    project, env, settings = hooked_project

    verdict = run_chain(project, "grep -rn PATTERN src/", settings=settings, env=env)

    assert "batch" in verdict.context.lower(), verdict.context


@pytest.mark.integration
def test_find_nudge_is_conditional_too(hooked_project):
    project, env, settings = hooked_project

    verdict = run_chain(project, "find . -name '*.py'", settings=settings, env=env)

    assert verdict.decision == "allow", str(verdict)
    assert "if the Glob tool is available" in verdict.context, verdict.context
