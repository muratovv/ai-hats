"""e2e (HATS-2100)

flow:   an agent opens a pull request, calls the harness task list, or adds a
        Python except that swallows the error
cmds:
    gh pr create --fill
    ls
    # TaskCreate — the harness task list; the session tracker is rack
    # Write src/mod.py — a new module whose except returns []
    # Write src/clean.py — a module with no handler
    # Edit src/already.py — a line away from a handler the file already has
expect: the pull request is asked and the reason says the write is reversible;
        ls passes; TaskCreate is denied and the reason names rack; the new
        silent except is denied and names the silent-ok mark; the clean module
        and the unrelated edit pass
why:    those prohibitions used to sit in the standing prompt, where a hook
        already held them. A chain test is the only place a second hook can
        override the verdict this change just moved
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import build_session_settings, run_chain, run_tool_chain

pytestmark = pytest.mark.integration

_INERT = "def f():\n    try:\n        return 1\n    except Exception:\n        return []\n"
_CLEAN = "def f():\n    return 1\n"
_ALREADY = _INERT + "\ndef other():\n    return 0\n"


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project. Assistant carries safety-guard; maintainer adds the python gate."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("expectation-home"))

    project = tmp_path_factory.mktemp("expectation-proj")
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
    (project / "src").mkdir()
    return project, env


def _settings(project, role: str):
    return build_session_settings(project, role, f"sid-{role}")


def test_a_pull_request_is_asked_as_reversible(hooked_project):
    """HATS-2100 — shared joins the ask, and the headline must not say irreversible."""
    project, env = hooked_project
    settings = _settings(project, "assistant")
    verdict = run_chain(project, "gh pr create --fill", settings=settings, env=env)
    assert verdict.decision == "ask", verdict
    assert "reversible" in verdict.reason, verdict
    assert "irreversible" not in verdict.reason, verdict
    assert "pre_bash_shared_state_guard" in verdict.hook, verdict


def test_an_ordinary_command_still_passes(hooked_project):
    project, env = hooked_project
    settings = _settings(project, "assistant")
    verdict = run_chain(project, "ls", settings=settings, env=env)
    assert not verdict.gated, verdict


def test_the_harness_task_list_is_denied_and_names_rack(hooked_project):
    """HATS-2100 — the six harness task tools are rack's, and the refusal says so."""
    project, env = hooked_project
    settings = _settings(project, "assistant")
    verdict = run_tool_chain(project, "TaskCreate", {"subject": "x"}, settings=settings, env=env)
    assert verdict.denied, verdict
    assert "rack" in verdict.reason, verdict
    assert "harness_task_gate" in verdict.hook, verdict


def test_a_destructive_command_is_still_denied(hooked_project):
    """A chain that asks about everything would pass the pull-request row for the wrong reason."""
    project, env = hooked_project
    settings = _settings(project, "assistant")
    verdict = run_chain(project, "rm -rf /", settings=settings, env=env)
    assert verdict.denied, verdict


def test_a_new_silent_except_is_denied(hooked_project):
    """HATS-2100 — maintainer's python gate refuses an edit that grows the inert set."""
    project, env = hooked_project
    settings = _settings(project, "maintainer")
    target = project / "src" / "mod.py"
    verdict = run_tool_chain(
        project,
        "Write",
        {"file_path": str(target), "content": _INERT},
        settings=settings,
        env=env,
    )
    assert verdict.denied, verdict
    assert "silent-ok" in verdict.reason, verdict
    assert "silent_fallback_gate" in verdict.hook, verdict


def test_a_clean_module_passes(hooked_project):
    project, env = hooked_project
    settings = _settings(project, "maintainer")
    target = project / "src" / "clean.py"
    verdict = run_tool_chain(
        project,
        "Write",
        {"file_path": str(target), "content": _CLEAN},
        settings=settings,
        env=env,
    )
    assert not verdict.gated, verdict


def test_an_edit_that_misses_an_existing_handler_passes(hooked_project):
    """The file already swallows. Changing another function must not be refused."""
    project, env = hooked_project
    settings = _settings(project, "maintainer")
    target = project / "src" / "already.py"
    target.write_text(_ALREADY)
    verdict = run_tool_chain(
        project,
        "Edit",
        {
            "file_path": str(target),
            "old_string": "def other():\n    return 0\n",
            "new_string": "def other():\n    return 2\n",
        },
        settings=settings,
        env=env,
    )
    assert not verdict.gated, verdict
