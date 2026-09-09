"""e2e (HATS-1899)

flow:   an agent whose working directory has slipped back to the MAIN checkout
        while a task worktree is live
cmds:
    git reset --hard HEAD~1          (in main)          -> denied
    git reset --hard HEAD~1          (in the worktree)  -> allowed
    cd <worktree> && git reset --hard HEAD~1            -> allowed
    git -C <main> reset --hard HEAD~1 (from a worktree) -> denied
expect: the composed PreToolUse Bash chain denies only where the command would
        destroy state in the main checkout
why:    edits in main are already denied and git state was not, so
        `git reset --hard HEAD~1` moved master by a commit; the reflog is what
        saved it, and that was the timing rather than the system
"""

from __future__ import annotations

import subprocess as sp
from pathlib import Path

import pytest

from _helpers.git import git, init_repo
from _helpers.hook_chain import (
    CLAUDE_PROJECT_DIR_VAR,
    build_session_settings,
    pretooluse_hooks,
    run_chain,
)

MARKER = "GUARDRAIL (worktree-isolation)"


@pytest.fixture(scope="module")
def chain(shared_launcher, tmp_path_factory):
    """A project with two commits and a real linked worktree beside it."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wt-git-home"))

    project = tmp_path_factory.mktemp("wt-git-proj")
    init_repo(project)
    (project / "tracked.txt").write_text("one\n")
    git(project, "add", "tracked.txt")
    git(project, "commit", "-m", "second commit", "--no-verify")

    res = sp.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert res.returncode == 0, f"self init failed:\n{res.stdout}\n{res.stderr}"
    settings = build_session_settings(project)

    worktree = tmp_path_factory.mktemp("wt-git-wt") / "linked"
    git(project, "worktree", "add", "-b", "task/probe", str(worktree))
    return project.resolve(), worktree.resolve(), settings, env


@pytest.mark.integration
def test_the_gate_is_on_the_composed_bash_chain(chain):
    """Positive control: every silence below must be a judgement, not an absence.

    The sibling module is checked too — the walk ships as its own file, and a
    materialization that dropped it would leave the gate fail-open on every call
    while the suite still looked green."""
    project, _worktree, settings, _env = chain
    hooks = pretooluse_hooks(settings, "Bash")
    mine = [h for h in hooks if "wt_git_gate.py" in h]
    assert mine, f"the gate is not on the composed Bash chain: {hooks}"

    materialized = Path(mine[0].replace(CLAUDE_PROJECT_DIR_VAR, f"{project}/").strip())
    assert materialized.is_file(), f"hook path does not resolve: {materialized}"
    sibling = materialized.parent / "shell_walk.py"
    assert sibling.is_file() and not sibling.is_symlink(), (
        f"the shared walk did not survive materialization: {sibling}"
    )


@pytest.mark.integration
def test_a_hard_reset_in_the_main_checkout_is_denied(chain):
    """The incident, verbatim: master moved by a commit from a slipped cwd (HATS-1899)."""
    project, worktree, settings, env = chain
    verdict = run_chain(project, "git reset --hard HEAD~1", settings=settings, env=env, cwd=project)
    assert verdict.denied, f"expected a deny, got {verdict}"
    assert MARKER in verdict.reason, verdict.reason
    assert str(worktree) in verdict.reason, (
        f"the sole live worktree should be named: {verdict.reason}"
    )


@pytest.mark.integration
def test_the_same_command_in_the_worktree_is_allowed(chain):
    """The negative half of the control. A gate that denied here would stop the
    revert-proof loop the project asks for."""
    project, worktree, settings, env = chain
    verdict = run_chain(
        project, "git reset --hard HEAD~1", settings=settings, env=env, cwd=worktree
    )
    assert not verdict.gated, f"a worktree reset must run: {verdict}"


@pytest.mark.integration
def test_a_cd_into_the_worktree_is_read_before_the_verdict(chain):
    """`cd <worktree> && git reset --hard` is what revert-proof.sh itself issues.

    Reading only the payload's cwd would deny it — the same cry-wolf failure this
    card exists to remove, reproduced inside the new guard."""
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"cd {worktree} && git reset --hard HEAD~1",
        settings=settings,
        env=env,
        cwd=project,
    )
    assert not verdict.gated, f"the cd went unread: {verdict}"


@pytest.mark.integration
def test_a_dash_c_aimed_at_main_from_a_worktree_is_denied(chain):
    """The false negative that matters: standing in the worktree does not make
    `git -C <main>` safe."""
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"git -C {project} reset --hard HEAD~1",
        settings=settings,
        env=env,
        cwd=worktree,
    )
    assert verdict.denied, f"expected a deny, got {verdict}"


@pytest.mark.integration
def test_a_separator_with_no_space_does_not_slip_past(chain):
    """`cd /tmp;git reset --hard` — one deleted space used to hide the whole line."""
    project, _worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"cd {project};git reset --hard HEAD~1",
        settings=settings,
        env=env,
        cwd=project,
    )
    assert verdict.denied, f"expected a deny, got {verdict}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "git status",  # reads nothing away
        "git reset tracked.txt",  # unstages an existing path
        "git reset",  # unstages everything; no ref moves
        "git checkout task/probe",  # git itself refuses when it would lose work
        "git branch -d task/probe",  # refuses a non-merged branch on its own
        "git restore --staged tracked.txt",  # index only, working tree untouched
        "echo 'git reset --hard HEAD~1'",  # the segment head is echo, not git
    ],
)
def test_the_safe_spellings_stay_out_of_the_way(chain, command):
    """The tier is narrow on purpose: a gate that fires on routine work is a gate
    somebody switches off."""
    project, _worktree, settings, env = chain
    verdict = run_chain(project, command, settings=settings, env=env, cwd=project)
    assert not verdict.gated, f"spurious deny for {command!r}: {verdict}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "git checkout -f task/probe",
        "git checkout -- tracked.txt",
        "git switch --discard-changes task/probe",
        "git restore tracked.txt",
        "git branch -D task/probe",
        "git clean -fd",
        "git reset --hard",
    ],
)
def test_every_destructive_spelling_in_the_tier_is_denied(chain, command):
    """One incident named `reset --hard`; the sibling spellings do the same thing."""
    project, _worktree, settings, env = chain
    verdict = run_chain(project, command, settings=settings, env=env, cwd=project)
    assert verdict.denied, f"expected a deny for {command!r}, got {verdict}"


@pytest.mark.integration
def test_the_kill_switch_silences_the_gate(chain):
    project, _worktree, settings, env = chain
    verdict = run_chain(
        project,
        "git reset --hard HEAD~1",
        settings=settings,
        env={**env, "AI_HATS_WT_GIT_OFF": "1"},
        cwd=project,
    )
    assert not verdict.gated, f"the kill switch did not silence the gate: {verdict}"
