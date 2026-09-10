"""e2e (HATS-1754)

flow:   an agent typing a gated move through a language runner — `uv run`,
        `uvx`, or `python -m <module>` — instead of the console script
cmds:
    uv run rack transition HATS-1 execute
    python3 -m ai_hats_rack transition HATS-1 execute
    uv run ai-hats wt merge task/x
expect: the composed chain raises the supervisor's question on every spelling,
        exactly as it does for the bare one
why:    measured 2026-08-20 — the chain returned NOTHING for these. `WRAPPERS`
        knew `sudo`/`env`/`timeout` but no language runner, so `slice_for` read
        the head binary as `uv` or `python3` and never found the guarded call.
        Both roads into master and the `plan -> execute` arrow were reachable by
        re-spelling the command, in silence. Revert the runner half and the
        parametrized assertions below go quiet rather than red elsewhere.
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import build_session_settings, run_chain  # noqa: E402
from _helpers.sessions import stand_in_session  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.consent]


@pytest.fixture(scope="module")
def runner_project(shared_launcher, tmp_path_factory):
    """A real project composed with a role that DECLARES both gated roads.

    ``assistant`` composes ``trait-agent``, which declares `plan->execute` on the
    rack and `pre-merge` on the wt — so the spellings below are judged against
    the shipped declaration, not a fixture written to match the assertion.
    """
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("runner-spelling-home"))

    project = tmp_path_factory.mktemp("runner-spelling-proj")
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


@pytest.fixture(scope="module")
def in_session(runner_project):
    """The chain, run from inside a session whose role declared consent."""
    project, env, settings = runner_project
    return project, stand_in_session(dict(env), project, "sid-runner"), settings


@pytest.mark.parametrize(
    "command",
    [
        # `uv run` and `uvx` are wrappers in the same sense as `timeout` — they
        # run another binary, and the guarded call sits behind them.
        "uv run rack transition HATS-1 execute",
        "uvx rack transition HATS-1 execute",
        "uv run ai-hats wt merge task/x",
        # `-m <module>`: the interpreter names a MODULE, not the console script.
        # `python -m ai_hats_rack` is the documented interpreter-tier entry
        # point (HATS-1263), and our own e2e shim tier drives it.
        "python3 -m ai_hats_rack transition HATS-1 execute",
        "python3 -m ai_hats wt merge task/x",
        # Both axes at once, the shape a venv-local run actually takes.
        "uv run python -m ai_hats_rack transition HATS-1 execute",
    ],
)
def test_a_gated_move_spelled_through_a_runner_still_raises_the_question(in_session, command):
    project, env, settings = in_session
    verdict = run_chain(project, command, settings=settings, env=env)

    assert verdict.gated, (
        f"{command!r} reached the tool with no question — the runner spelling "
        f"walked past the guard: {verdict}"
    )


@pytest.mark.parametrize(
    "command",
    [
        # Positive control: a chain that gated EVERYTHING would pass the
        # assertions above while proving nothing.
        "uv run rack ls",
        "uv run pytest -k 'wt merge'",
        # The payload of an interpreter is ONE token, and must not be read as a
        # command of its own — that is how a quoted argument synthesised a call
        # that was never issued (HATS-1253 R5). What `python -c` can reach is
        # unbounded by construction and is INFORMED about by the permission
        # lint, never gated here (HATS-1754 defect 3).
        "python3 -c \"print('rack transition HATS-1 execute')\"",
    ],
)
def test_what_only_looks_like_a_gated_move_is_left_alone(in_session, command):
    project, env, settings = in_session
    verdict = run_chain(project, command, settings=settings, env=env)

    assert not verdict.gated, f"{command!r} was gated — the probe proves nothing: {verdict}"
