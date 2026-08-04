"""HATS-1268 — claude runtime hooks execute from the session skill mirror.

Two properties the flat ``library/hooks/`` copy could not hold, asserted over a
real composed session (``dev_rule_e2e_gate``; a per-channel test proves nothing
about the composite — HATS-1113):

1. every wired command resolves inside the session tree, with the data files
   its skill ships still beside it;
2. a hook that fires through the chain still WRITES to the bypass journal.

(2) is the one that matters: the journal helper is reached as a sibling, and a
missing sibling degrades to a stub that prints to stderr and returns cleanly —
indistinguishable from success by exit code alone. Asserting "the hook ran"
would pass with the journal dead.

Both share one module-scoped project: ``self init`` costs ~1 min, and the two
assertions are about the same materialized session.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import build_session_settings, pretooluse_hooks, run_chain

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
    """Fail-under-revert: point _desired_runtime_entries back at
    ``_lib_hooks_dir`` / ``managed_runtime_hook_filename`` and every path below
    lands in ``.agent/ai-hats/library/hooks/`` instead."""
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
        assert "library/hooks" not in command, f"still wired to the flat copy: {command}"


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
