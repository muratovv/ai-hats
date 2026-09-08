"""e2e (HATS-1856)

flow:   an agent standing in a linked worktree launching a check runner
cmds:
    /main/.venv/bin/python -m pytest tests/
    ./.venv/bin/python -m pytest tests/
expect: the composed PreToolUse Bash chain nudges on the first (the interpreter
        belongs to another checkout) and stays silent on the second, never gating
why:    an interpreter from the wrong checkout makes the run measure sources the
        agent did not write, and the two existing guards are blind to it
"""

from __future__ import annotations

import subprocess as sp
import sys
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


def _fake_venv(root: Path) -> None:
    """A venv shaped like the real one: what the guard resolves are these files.

    ``python``/``python3`` are SYMLINKS to the base interpreter, which is what
    ``python -m venv`` writes and the single property that decides this guard's
    verdict — a stub file resolves to itself and makes every symlink-resolution
    bug invisible. They point at a working interpreter rather than exiting 0
    because putting this bin on PATH would otherwise shadow the ``python3`` the
    hook's own shebang resolves, and the guard would go silent for a reason that
    has nothing to do with its verdict.

    ``pyvenv.cfg`` is the marker CPython itself reads to decide ``sys.prefix``.
    """
    venv = root / ".venv"
    bindir = venv / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    base = Path(sys.executable).resolve()  # what `python -m venv` links bin/python to
    (venv / "pyvenv.cfg").write_text(
        f"home = {base.parent}\ninclude-system-site-packages = false\n"
    )
    for name in ("python", "python3"):
        exe = bindir / name
        exe.unlink(missing_ok=True)
        exe.symlink_to(base)
    for name in ("pytest", "ruff"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)


@pytest.fixture(scope="module")
def chain(shared_launcher, tmp_path_factory):
    """An initialized project, a real linked worktree, and the composed settings.

    Both checkouts get a venv — the whole point is that only one of them is the
    right one to run from, and nothing but the path says which.
    """
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wt-interp-home"))

    project = tmp_path_factory.mktemp("wt-interp-proj")
    init_repo(project)
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

    worktree = tmp_path_factory.mktemp("wt-interp-wt") / "linked"
    git(project, "worktree", "add", "-b", "task/probe", str(worktree))

    _fake_venv(project)
    _fake_venv(worktree)
    return project.resolve(), worktree.resolve(), settings, env


@pytest.mark.integration
def test_the_guard_is_on_the_composed_bash_chain(chain):
    """Positive control for every assertion below.

    Without it a silent chain reads as a guard that judged and approved, when it
    may simply never have been loaded.
    """
    project, _worktree, settings, _env = chain
    hooks = pretooluse_hooks(settings, "Bash")
    mine = [h for h in hooks if "wt_interpreter_gate.py" in h]
    assert mine, f"the guard is not on the composed Bash chain: {hooks}"

    # The shared runner list is a symlink in the library. If materialization
    # carried the LINK rather than the file, the guard would fall back to its
    # embedded mirror and journal a degraded verdict on every single Bash call —
    # a failure that stays invisible precisely because the mirror still works.
    materialized = Path(mine[0].replace(CLAUDE_PROJECT_DIR_VAR, f"{project}/").strip())
    assert materialized.is_file(), f"hook path does not resolve: {materialized}"
    sibling = materialized.parent / "test_runners.json"
    assert sibling.is_file() and not sibling.is_symlink(), (
        f"the shared runner list did not survive materialization: {sibling}"
    )


@pytest.mark.integration
def test_foreign_interpreter_in_a_worktree_is_named(chain):
    """The positive half of the control: a provable mismatch must warn."""
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"{project}/.venv/bin/python -m pytest tests/",
        settings=settings,
        env=env,
        cwd=worktree,
    )
    assert MARKER in verdict.context, f"expected a nudge, got {verdict.context!r}"
    # Both resolved paths, so the agent adjudicates nothing (HATS-1856 AC-2).
    assert str(worktree) in verdict.context, verdict.context
    assert f"{project}/.venv/bin/python" in verdict.context, verdict.context
    # Non-blocking contract, same as its Bash-chain neighbours.
    assert not verdict.gated, f"this guard must never gate: {verdict}"


@pytest.mark.integration
def test_a_path_spelled_console_script_is_read(chain):
    """Mirror of the silent `.venv/bin/pytest` case, and what proves it sound.

    A runner written behind a `/` has no whitespace in front of its name — the
    shape the guard's cheap pre-filter once skipped, which made the silent case
    pass for a reason that had nothing to do with where it resolved.
    """
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"{project}/.venv/bin/pytest tests/",
        settings=settings,
        env=env,
        cwd=worktree,
    )
    assert MARKER in verdict.context, f"path-spelled runner went unread: {verdict.context!r}"
    assert f"{project}/.venv/bin/pytest" in verdict.context, verdict.context


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "./.venv/bin/python -m pytest tests/",  # the spelling SKILL.md prescribes
        ".venv/bin/python -m pytest tests/",  # same, without the leading dot-slash
        ".venv/bin/pytest tests/",  # the console script in the same venv
    ],
)
def test_the_worktrees_own_interpreter_is_silent(chain, command):
    """The negative half of the control, and the one that would rot unnoticed."""
    _project, worktree, settings, env = chain
    verdict = run_chain(worktree, command, settings=settings, env=env, cwd=worktree)
    assert MARKER not in verdict.context, f"spurious nudge for {command!r}: {verdict.context!r}"


@pytest.mark.integration
def test_the_documented_path_remedy_is_not_second_guessed(chain):
    """`PATH=<wt>/.venv/bin:$PATH <runner>` is what _checkout_guard.py recommends.

    Nudging the fix the project prints teaches the agent to distrust it, so the
    guard resolves THROUGH the override rather than reading the bare name.
    """
    _project, worktree, settings, env = chain
    verdict = run_chain(
        worktree,
        f'PATH="{worktree}/.venv/bin:$PATH" pytest tests/',
        settings=settings,
        env=env,
        cwd=worktree,
    )
    assert MARKER not in verdict.context, f"the remedy was nudged: {verdict.context!r}"


@pytest.mark.integration
def test_a_cd_into_the_worktree_still_counts(chain):
    """cwd says main checkout; the command moves before it runs (HATS-1856).

    Reading only the payload's cwd would classify this as a legitimate main-checkout
    run and stay silent.
    """
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"cd {worktree} && {project}/.venv/bin/python -m pytest tests/",
        settings=settings,
        env=env,
        cwd=project,
    )
    assert MARKER in verdict.context, f"the cd went unread: {verdict.context!r}"
    assert not verdict.gated, verdict


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "ruff check src/",  # ruff imports nothing of the project: any ruff lints these files
        "make lint",  # a make target that names no check resolves no project import
        "make help",
        "git status",  # no runner at all
    ],
)
def test_commands_that_prove_nothing_stay_silent(chain, command):
    """The guard's contract: it speaks only where the mismatch is provable.

    Every command here would run through an executable outside the worktree, and
    not one of them is therefore wrong — which is exactly why a nudge would cost
    more than it buys.
    """
    _project, worktree, settings, env = chain
    verdict = run_chain(worktree, command, settings=settings, env=env, cwd=worktree)
    assert MARKER not in verdict.context, f"spurious nudge for {command!r}: {verdict.context!r}"


@pytest.mark.integration
def test_a_make_test_target_resolves_its_recipes_runner(chain):
    """`make test` names no interpreter, so the guard resolves the one its recipe
    would reach for — and says that is what it did."""
    project, worktree, settings, env = chain
    env_with_main_first = {**env, "PATH": f"{project}/.venv/bin:{env.get('PATH', '')}"}
    verdict = run_chain(
        project,
        "make integration-test",
        settings=settings,
        env=env_with_main_first,
        cwd=worktree,
    )
    assert MARKER in verdict.context, f"expected a nudge, got {verdict.context!r}"
    assert f"{project}/.venv/bin/pytest" in verdict.context, verdict.context


@pytest.mark.integration
def test_a_script_named_through_bash_is_still_the_script(chain):
    """`bash scripts/gates.sh` runs it as surely as naming it does."""
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        "bash scripts/gates.sh",
        settings=settings,
        env={**env, "PATH": f"{project}/.venv/bin:{env.get('PATH', '')}"},
        cwd=worktree,
    )
    assert MARKER in verdict.context, f"the script went unread: {verdict.context!r}"


@pytest.mark.integration
def test_the_main_checkouts_own_interpreter_is_silent(chain):
    """Standing in the main checkout, its interpreter is the correct one."""
    project, _worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"{project}/.venv/bin/python -m pytest tests/",
        settings=settings,
        env=env,
        cwd=project,
    )
    assert MARKER not in verdict.context, f"main-checkout run nudged: {verdict.context!r}"


@pytest.mark.integration
def test_the_kill_switch_silences_the_guard(chain):
    project, worktree, settings, env = chain
    verdict = run_chain(
        project,
        f"{project}/.venv/bin/python -m pytest tests/",
        settings=settings,
        env={**env, "AI_HATS_WT_INTERP_OFF": "1"},
        cwd=worktree,
    )
    assert MARKER not in verdict.context, f"kill switch ignored: {verdict.context!r}"
