"""e2e (HATS-1798)

flow:   an agent preserving a pipeline's runner status, in the shell the Bash tool
        actually runs (zsh), driven through the whole composed PreToolUse chain
cmds:
    ruff check src/ | tail; exit ${PIPESTATUS[0]}
    ruff check src/ | tail; exit ${pipestatus[1]}
expect: the bash-only spelling is nudged (in zsh it returns 0 for every run) and the
        zsh-correct spelling is not
why:    the guard exempted any command containing PIPESTATUS, so it stayed silent on a
        form that reads a red run as green, and nudged the only spelling that works
        here — the exemption has to know which shell it is guarding
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import build_session_settings, run_chain  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash hook chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("shellaware-home"))

    project = tmp_path_factory.mktemp("shellaware-proj")
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


def _nudge(project, env, settings, command: str) -> str:
    verdict = run_chain(project, command, settings=settings, env=env)
    assert not verdict.gated, f"a hygiene nudge must never gate the call: {verdict}"
    return verdict.context


@pytest.mark.integration
def test_bash_only_pipestatus_is_nudged(hooked_project):
    """``${PIPESTATUS[0]}`` in zsh is ``exit ""`` -> 0: masking, not preservation."""
    project, env, settings = hooked_project
    ctx = _nudge(project, env, settings, "ruff check src/ | tail; exit ${PIPESTATUS[0]}")
    assert "exit code masking detected" in ctx, (
        "the guard stayed silent on the bash-only spelling — in the tool's zsh it "
        f"returns 0 for every run, red ones included. context={ctx!r}"
    )
    assert "pipestatus" in ctx, f"the nudge must name the spelling that works here: {ctx!r}"


@pytest.mark.integration
def test_zsh_correct_pipestatus_is_not_nudged(hooked_project):
    """``${pipestatus[1]}`` is the only spelling that preserves the status here."""
    project, env, settings = hooked_project
    ctx = _nudge(project, env, settings, "ruff check src/ | tail; exit ${pipestatus[1]}")
    assert "exit code masking detected" not in ctx, (
        f"the guard nudged the zsh-correct spelling — the one that works: {ctx!r}"
    )


@pytest.mark.integration
def test_pipefail_stays_exempt(hooked_project):
    """Positive control: ``set -o pipefail`` is correct in both shells."""
    project, env, settings = hooked_project
    ctx = _nudge(project, env, settings, "set -o pipefail; ruff check src/ | tail")
    assert "exit code masking detected" not in ctx, f"pipefail must stay exempt: {ctx!r}"


@pytest.mark.integration
def test_nudge_teaches_the_spelling_that_works_here(hooked_project):
    """The nudge is the only delivery that fires at the moment of the mistake.

    It used to prescribe ``${PIPESTATUS[0]}`` flatly — the form that returns 0
    in this shell. It must still name it, but as the trap it is, next to the
    spelling that works.
    """
    project, env, settings = hooked_project
    ctx = _nudge(project, env, settings, "ruff check src/ | tail")
    assert "exit code masking detected" in ctx, f"expected a nudge: {ctx!r}"
    assert "pipefail" in ctx, f"the cure correct in both shells must lead: {ctx!r}"
    assert "bash-only" in ctx, (
        f"naming PIPESTATUS without the bash-only qualifier prescribes it: {ctx!r}"
    )
    assert "${pipestatus[1]}" in ctx, f"the zsh spelling must be given: {ctx!r}"
