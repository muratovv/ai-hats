"""e2e (HATS-1268)

flow:   an agent executing tools in a session with materialized runtime hooks
cmds:
    ai-hats self init -p claude -r maintainer --no-wizard
expect: hook scripts resolve inside session tree alongside sibling data files and write
        bypass records
why:    without session-tree hook resolution, flattened hook scripts lose sibling data
        files and bypass logging
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import build_session_settings, pretooluse_hooks, run_chain

pytestmark = pytest.mark.guards

JOURNAL_REL = ".git/ai-hats/bypasses.jsonl"


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real maintainer project with a built session — maintainer composes
    worktree-isolation, the skill whose sibling data file the flatten dropped."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("session-tree-home"))

    project = tmp_path_factory.mktemp("session-tree-proj")
    for args in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "t@e.x"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=str(project), check=True)  # noqa: S603

    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "maintainer", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    return project, env, build_session_settings(project, role="maintainer")


@pytest.mark.integration
def test_every_wired_command_resolves_inside_the_session_tree(hooked_project):
    """Fail-under-revert: point _desired_runtime_entries back at a shared
    managed directory and every path below leaves the session tree. The old
    negative form (``"library/hooks" not in command``) became untestable when
    HATS-1480 deleted that directory, so the assertion is positive now."""
    project, _env, settings = hooked_project
    commands = pretooluse_hooks(settings, "Bash")

    # Positive control: an empty chain would satisfy every assertion below.
    assert commands, f"no Bash PreToolUse hooks wired in {settings}"

    session_root = settings.parent
    for command in commands:
        script = Path(command.split()[0])
        assert script.is_absolute(), command
        assert script.is_file(), f"wired command does not exist on disk: {command}"
        assert script.is_relative_to(session_root), (
            f"command escapes the session tree: {command} (root {session_root})"
        )
        assert "plugin/skills" in command, (
            f"wired command is not in the session skill mirror: {command}"
        )


@pytest.mark.integration
def test_a_skills_data_file_is_still_beside_its_hook(hooked_project):
    """The card's headline bug: flattening to <skill>-<basename> dropped
    ``code_extensions.json``, and wt_gate.py fell through to _DEFAULT_LANGS in
    silence. Fail-under-revert: same revert as above — the sibling is gone."""
    _project, _env, settings = hooked_project
    wired = [c for c in pretooluse_hooks(settings, "Edit") if "wt_gate" in c]
    assert wired, "worktree-isolation wt_gate is not wired — scene did not play"

    gate = Path(wired[0].split()[0])
    assert (gate.parent / "code_extensions.json").is_file(), (
        f"sibling data file missing next to {gate}"
    )


@pytest.mark.integration
def test_a_hook_reached_through_the_chain_writes_the_bypass_journal(hooked_project):
    """R2: 'the hook ran' is not the criterion — 'the hook recorded' is.

    bypass_journal is package data resolved as a SIBLING; the flat copy used to
    manufacture that adjacency. Fail-under-revert: delete
    ``core/skills/safety-guard/hooks/bypass_journal.sh`` and the guard falls
    through to its NOT RECORDED stub, which still exits 0 — this test is what
    tells the two apart.
    """
    project, env, settings = hooked_project
    journal = project / JOURNAL_REL
    before = len(journal.read_text().splitlines()) if journal.is_file() else 0

    verdict = run_chain(
        project,
        "git push origin master",
        settings=settings,
        env=env,
        ack="AI_HATS_SHARED_STATE_ACK",
    )

    assert journal.is_file(), (
        f"no bypass journal at {journal} — the guard fell through to its stub "
        f"(chain verdict: {verdict})"
    )
    lines = journal.read_text().splitlines()
    assert len(lines) > before, f"journal did not grow: {lines}"
    assert json.loads(lines[-1])["cmd"] == "git push origin master"
