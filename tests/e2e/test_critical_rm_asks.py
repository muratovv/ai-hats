"""e2e (HATS-2044, HATS-2054)

flow:   an agent removes its own working directory in a session whose role declares fs.remove,
        with trait-agent and on the base trait alone
cmds:
    rm -rf <project>
    consent all 30
    consent fs.remove 30
expect: the composed chain asks, with no ticket rewrite; a grant, the consent ack and an
        unparsable line never turn the question into silence; the verb refuses fs.remove
        by name; an ordinary removal is not asked about; a role without trait-agent asks
        the same question with no command wrapper armed
why:    Claude Code's own prompt for a critical-path rm times out into a refusal, so the
        launch switches that timer off and the gate becomes the only question; a gate
        that goes quiet here hands the removal to the harness's classifier (ADR-0039)
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import build_session_settings, run_chain, run_unasked

pytestmark = [pytest.mark.integration, pytest.mark.consent]

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_ID = "e2e-critical-rm"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / "build").mkdir()
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


def _session_env(project: Path, checkout_bin: Path, session_id: str, role: str) -> dict:
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_wrapped_session

    from ai_hats.consent_wrapper import CONFIG_ENV

    e = os.environ.copy()
    for stale in ("AI_HATS_CONSENT_ACK", "AI_HATS_CONSENT_TICKET", "AI_HATS_YOLO", CONFIG_ENV):
        e.pop(stale, None)
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    e["PATH"] = os.pathsep.join([str(checkout_bin), e.get("PATH", "")])
    return stand_in_wrapped_session(e, project, session_id, role=role)


@pytest.fixture
def env(project: Path, checkout_bin: Path) -> dict:
    return _session_env(project, checkout_bin, SESSION_ID, "assistant")


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


def _declared(env: dict) -> list[dict]:
    identity = json.loads(env["AI_HATS_SESSION_IDENTITY"])
    record = Path(identity["session_dir"]) / "role_materialization.json"
    return json.loads(record.read_text(encoding="utf-8")).get("consent", [])


def test_the_role_declares_the_point(env):
    """Control: every assertion below is only about fs.remove if the role declared it."""
    rows = [r for r in _declared(env) if r.get("path") == ["fs.remove"]]
    assert [r.get("selector") for r in rows] == ["critical-path"], _declared(env)


def test_removing_the_working_directory_is_a_question(project, settings, env):
    verdict = run_chain(project, f"rm -rf {project}", settings=settings, env=env)

    assert verdict.decision == "ask", f"the removal went unasked: {verdict}"
    assert str(project) in verdict.reason, verdict.reason
    assert not verdict.updated_input, "a hook-only operation carries no ticket rewrite"


@pytest.mark.parametrize("command", ["rm -rf .", "rm -rf ./*", 'rm -rf "$WORK"/*', "rm -rf /usr"])
def test_every_spelling_of_a_critical_target_is_a_question(project, settings, env, command):
    assert run_chain(project, command, settings=settings, env=env).decision == "ask", command


def test_an_ordinary_removal_is_not_asked_about(project, settings, env):
    verdict = run_chain(project, "rm -rf build", settings=settings, env=env)
    assert not verdict.gated, f"an ordinary removal was gated: {verdict}"


def test_an_unparsable_line_that_removes_is_a_question(project, settings, env):
    verdict = run_chain(project, 'rm -rf "unbalanced', settings=settings, env=env)
    assert verdict.decision == "ask", f"the guard went quiet on what it could not read: {verdict}"


def test_the_consent_ack_does_not_silence_it(project, settings, env):
    verdict = run_chain(
        project, f"rm -rf {project}", settings=settings, env=env, ack="AI_HATS_CONSENT_ACK"
    )
    assert verdict.decision == "ask", f"the ack silenced an ungrantable point: {verdict}"


def test_a_live_grant_does_not_silence_it(project, settings, env):
    issued = run_unasked(project, "consent all 30", env=env)
    assert issued.returncode == 0, issued.stderr or issued.stdout
    assert "fs.remove" not in issued.stdout, f"`all` granted the ungrantable: {issued.stdout}"

    # Positive control: the same window really does silence a grantable point.
    merge = run_chain(project, "ai-hats wt merge task/x", settings=settings, env=env)
    assert not merge.gated, f"the grant is not live, so this proves nothing: {merge}"

    verdict = run_chain(project, f"rm -rf {project}", settings=settings, env=env)
    assert verdict.decision == "ask", f"a grant silenced an ungrantable point: {verdict}"


def test_a_grant_on_its_neighbour_does_not_silence_it(project, settings, env):
    """The grant answers the merge; the removal chained after it is still asked."""
    assert run_unasked(project, "consent wt.merge 30", env=env).returncode == 0

    line = f"ai-hats wt merge task/x && rm -rf {project}"
    verdict = run_chain(project, line, settings=settings, env=env)
    assert verdict.decision == "ask", f"the neighbour's grant silenced the removal: {verdict}"


def test_the_verb_refuses_fs_remove_by_name(project, env):
    refused = run_unasked(project, "consent fs.remove 30", env=env)
    assert refused.returncode == 1, refused.stdout
    assert "cannot be granted" in refused.stderr, refused.stderr


BASE_ROLE = "initial-wizard"


@pytest.fixture
def base_env(project: Path, checkout_bin: Path) -> dict:
    return _session_env(project, checkout_bin, f"{SESSION_ID}-base", BASE_ROLE)


def test_a_role_without_trait_agent_declares_the_point_and_arms_no_wrapper(base_env):
    """Control for the next test, and the wrapper stays out: fs.remove has no shim to arm."""
    from ai_hats.consent_wrapper import CONFIG_ENV

    rows = [r for r in _declared(base_env) if r.get("path") == ["fs.remove"]]
    assert [r.get("selector") for r in rows] == ["critical-path"], _declared(base_env)
    assert CONFIG_ENV not in base_env, "a hook-only declaration armed the command wrapper"


def test_a_role_without_trait_agent_asks_about_its_working_directory(project, base_env):
    settings = build_session_settings(project, role=BASE_ROLE, session_id="sid-base-hooks")

    verdict = run_chain(project, f"rm -rf {project}", settings=settings, env=base_env)

    assert verdict.decision == "ask", f"the removal went unasked: {verdict}"
    assert str(project) in verdict.reason, verdict.reason
