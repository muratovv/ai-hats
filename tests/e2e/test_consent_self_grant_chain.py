"""e2e (HATS-1639, HATS-1682)

flow:   an agent obtaining consent without the supervisor — inline in its own Bash
        call, or by typing a shape the guard goes quiet on
cmds:
    AI_HATS_PLAN_ACK=1 rack transition HATS-1 execute
    rack transition SBX-1 done --log "ask rack later"
expect: the composed PreToolUse chain refuses both; consent set in the ENVIRONMENT
        still works, and an unprefixed transition still passes
why:    rack reads these acks itself, so an inline prefix reaches them; and on a point
        the role DECLARED, silence is what the permission flow reads as allow — the
        same grant by another road (HATS-1682 A4)
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import (  # noqa: E402
    build_session_settings,
    run_chain,
)

pytestmark = [pytest.mark.integration, pytest.mark.consent]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Read by the rack PROCESS, so an inline prefix reaches them (unlike hook-read
#: AI_HATS_SHARED_STATE_ACK, where it cannot) — HATS-1639. `AI_HATS_CONSENT_ACK`
#: joined at HATS-1682: same class, and its absence here left a hole (T5).
PROCESS_READ_ACKS = ["AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK", "AI_HATS_CONSENT_ACK"]


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


# --- the same grant by another road: silence on a point the role declared ---

#: `review->done` — the agent trait declares it, and it carries the merge.
DECLARED_MOVE = "done"

#: Where the guard's journal lands, relative to the project it judged.
JOURNAL = Path(".git") / "ai-hats" / "bypasses.jsonl"


@pytest.fixture(scope="module")
def declared_project(tmp_path_factory):
    """A checkout whose session declares consent, as a real launch writes it.

    ``hooked_project`` above carries no session at all, so nothing is declared
    and the guard is right to say nothing. Silence only becomes a grant once a
    point IS declared — which is what this fixture supplies (HATS-1682 A4).
    """
    from _helpers.git import git as _git
    from _helpers.sessions import stand_in_session

    project = tmp_path_factory.mktemp("declared-proj")
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    _git(project, "commit", "-m", "init", "--allow-empty")

    env = os.environ.copy()
    env["HOME"] = str(tmp_path_factory.mktemp("declared-home"))
    env = stand_in_session(env, project, "e2e-consent-silence")
    return project, env, build_session_settings(project)


def _journal_since(project: Path, before: str) -> str:
    text = (project / JOURNAL).read_text(encoding="utf-8") if (project / JOURNAL).exists() else ""
    return text[len(before) :]


@pytest.mark.parametrize(
    ("shape", "names_the_obstacle"),
    [
        pytest.param(
            'rack transition SBX-1 {move} --log "ask rack later"',
            "option value",
            id="the-word-rack-inside-an-option-value",
        ),
        pytest.param(
            "cd {outside} && rack transition SBX-1 {move}",
            "project checkout",
            id="run-from-outside-any-checkout",
        ),
    ],
)
def test_a_declared_point_the_guard_cannot_ask_about_is_refused(
    declared_project, tmp_path, shape, names_the_obstacle
):
    """Both shapes went QUIET, and quiet on a declared point is an allow.

    Measured on the shipped hook: the first finds two spellings of `rack` for
    one call and skips the rewrite, the second lands the ticket store outside
    any git dir — and the command then simply ran, merge included (HATS-1682
    A4). The refusal has to name what is in the way, or the agent is told `no`
    with nowhere to go.
    """
    project, env, settings = declared_project
    outside = tmp_path / "not-a-checkout"
    outside.mkdir()
    command = shape.format(move=DECLARED_MOVE, outside=outside)

    verdict = run_chain(project, command, settings=settings, env=env)

    assert verdict.decision == "deny", f"{command!r} was not refused: {verdict}"
    assert verdict.hook == "safety_gate.py", f"another hook answered: {verdict}"
    assert names_the_obstacle in verdict.reason, (
        f"the refusal does not say what is in the way: {verdict}"
    )


def test_the_same_move_in_its_plain_shape_still_asks(declared_project):
    """Control: the refusals above are about the SHAPE, not about the move.

    Without this, a guard that had simply started denying the whole edge would
    look identical to one that kept asking wherever it can.
    """
    project, env, settings = declared_project
    verdict = run_chain(
        project, f"rack transition SBX-1 {DECLARED_MOVE}", settings=settings, env=env
    )
    assert verdict.decision == "ask", f"the declared point stopped asking: {verdict}"


def test_a_session_report_written_before_the_declaration_stays_silent(declared_project, tmp_path):
    """An absent `consent` key is an absent declaration, not a corrupt file.

    Read by index it raised `KeyError`, which the guard recorded as a fail-open
    and answered with silence — so a session started before this branch had
    consent off for the rest of its life (HATS-1682 B5, measured). The verdict
    is silence either way, so only the journal tells the two apart.
    """
    project, env, settings = declared_project
    old_session = tmp_path / "pre-1682-session"
    old_session.mkdir()
    (old_session / "role_materialization.json").write_text(
        json.dumps({"role": "assistant", "checks": []}), encoding="utf-8"
    )
    stale = {
        **env,
        "AI_HATS_SESSION_IDENTITY": json.dumps({"v": 1, "session_dir": str(old_session)}),
    }
    before = (project / JOURNAL).read_text(encoding="utf-8") if (project / JOURNAL).exists() else ""

    verdict = run_chain(
        project, f"rack transition SBX-1 {DECLARED_MOVE}", settings=settings, env=stale
    )

    appended = _journal_since(project, before)
    assert not verdict.gated, f"a session that declared nothing was gated: {verdict}"
    assert "fail-open" not in appended, (
        f"an absent declaration was recorded as an unreadable one:\n{appended}"
    )
