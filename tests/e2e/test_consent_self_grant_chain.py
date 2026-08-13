"""e2e (HATS-1639)

flow:   an agent granting itself supervisor consent inline, in the same Bash call
cmds:
    AI_HATS_PLAN_ACK=1 rack transition HATS-1 execute
expect: the composed PreToolUse chain refuses it; consent set in the ENVIRONMENT still
        works, and an unprefixed transition still passes
why:    rack reads these two acks itself, so unlike hook-read AI_HATS_SHARED_STATE_ACK an
        inline prefix reaches them and the agent approves its own transition
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import (  # noqa: E402
    build_session_settings,
    run_chain,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Read by the rack PROCESS, so an inline prefix reaches them (unlike the hook-read
#: AI_HATS_SHARED_STATE_ACK, where the prefix cannot work by construction) — HATS-1639.
PROCESS_READ_ACKS = ["AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK"]


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash PreToolUse chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("self-grant-home"))

    project = tmp_path_factory.mktemp("self-grant-proj")
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


@pytest.mark.parametrize("ack", PROCESS_READ_ACKS)
def test_inline_consent_self_grant_is_refused_by_the_chain(hooked_project, ack):
    """The agent may not hand itself the supervisor's consent inside its own command."""
    project, env, settings = hooked_project
    verdict = run_chain(
        project, f"{ack}=1 rack transition HATS-1 execute", settings=settings, env=env
    )
    assert verdict.gated, f"{ack} inline self-grant was ALLOWED: {verdict}"
    assert ack in verdict.reason, f"the refusal does not name {ack}: {verdict}"


def test_the_established_yolo_self_grant_is_still_refused(hooked_project):
    """Positive control: without it, a chain that refuses everything looks identical
    to a chain that gained the new rule."""
    project, env, settings = hooked_project
    verdict = run_chain(project, "AI_HATS_YOLO=1 rm -rf /tmp/x", settings=settings, env=env)
    assert verdict.gated, f"the pre-existing YOLO self-grant was ALLOWED: {verdict}"


def test_an_unprefixed_transition_still_passes(hooked_project):
    """Positive control: the guard targets the inline grant, not rack itself."""
    project, env, settings = hooked_project
    verdict = run_chain(project, "rack transition HATS-1 execute", settings=settings, env=env)
    assert not verdict.gated, f"a plain transition was blocked: {verdict}"


@pytest.mark.parametrize("ack", PROCESS_READ_ACKS)
def test_consent_from_the_environment_still_works(hooked_project, ack):
    """The user's channel survives: pre-approval exported into the session env is
    NOT an inline self-grant and must keep passing."""
    project, env, settings = hooked_project
    verdict = run_chain(
        project, "rack transition HATS-1 execute", settings=settings, env=env, ack=ack
    )
    assert not verdict.gated, f"environment-set {ack} was blocked: {verdict}"
