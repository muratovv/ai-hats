"""e2e (HATS-1944)

flow:   an agent handing itself a GATE flag inline, the way the gate's own red
        verdict used to invite, and the reading of that same flag it must keep
cmds:
    AI_HATS_RED_MASTER_ACK=1 make done-gate
    grep -rn AI_HATS_RED_MASTER_ACK scripts/
expect: the composed PreToolUse chain refuses the grant and leaves the grep alone
why:    gate flags read by a script inside `make` or a git hook are reached by an
        inline prefix (unlike hook-read flags), so `master-ci`'s hatch was
        self-servable and the smoke gate's was skipped on four commits that way
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import (  # noqa: E402
    build_session_settings,
    run_chain,
)

pytestmark = pytest.mark.integration

#: Read by a PROCESS inside `make` or a git hook, so an inline prefix reaches them —
#: unlike the hook-read acks of HATS-1639, where it cannot (HATS-1944). One per
#: suffix the shape rule covers; the off-convention names ride in the unit tests.
PROCESS_READ_GATE_FLAGS = [
    "AI_HATS_RED_MASTER_ACK",
    "AI_HATS_SMOKE_SKIP",
    "AI_HATS_WT_GATE_OFF",
]


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash PreToolUse chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("ack-grant-home"))

    project = tmp_path_factory.mktemp("ack-grant-proj")
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


@pytest.mark.parametrize("flag", PROCESS_READ_GATE_FLAGS)
def test_an_inline_gate_flag_is_refused_by_the_chain(hooked_project, flag):
    """The measured hole: before this guard, the prefix passed with nothing firing."""
    project, env, settings = hooked_project
    verdict = run_chain(project, f"{flag}=1 make done-gate", settings=settings, env=env)
    assert verdict.gated, f"{flag} inline self-grant was ALLOWED: {verdict}"
    assert flag in verdict.reason, f"the refusal does not name {flag}: {verdict}"


def test_the_refusal_routes_to_the_road_that_does_exist(hooked_project):
    """A deny with nowhere to go leaves the agent only blunt instruments."""
    project, env, settings = hooked_project
    verdict = run_chain(
        project, "AI_HATS_RED_MASTER_ACK=1 make done-gate", settings=settings, env=env
    )
    assert "LAUNCHES" in verdict.reason, f"no road named: {verdict}"
    assert "red-attribution" in verdict.reason, f"no procedure named: {verdict}"


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn AI_HATS_RED_MASTER_ACK scripts/",
        'echo "AI_HATS_RED_MASTER_ACK=1 make done-gate"',
        "make done-gate",
    ],
)
def test_reading_about_a_flag_is_not_granting_it(hooked_project, command):
    """The precision that keeps the guard worth having.

    A guard that refuses `grep` for its own flag name teaches the agent to route
    around it, and it can no longer read the code that explains itself."""
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    assert not verdict.gated, f"wrongly gated: {command!r} -> {verdict}"


def test_an_established_deny_still_denies(hooked_project):
    """Positive control: a chain that refuses everything would pass the tests above
    for the wrong reason, and a chain wired to nothing would pass the allow rows."""
    project, env, settings = hooked_project
    verdict = run_chain(project, "rm -rf /", settings=settings, env=env)
    assert verdict.gated, f"the chain is not gating at all: {verdict}"
