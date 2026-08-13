"""e2e (HATS-1647)

flow:   an agent edits a task card by hand instead of going through `rack`,
        while a teammate keeps authoring the same task's plan.md
cmds:
    rack transition HATS-1647 execute --log 'moved by the sanctioned writer'
    sed -i '' 's/^state:.*/state: done/' <ai_hats_dir>/tracker/backlog/tasks/HATS-1647/task.yaml  # no-resolve: the raw mutation the gate refuses
expect: writes under `<ai_hats_dir>/tracker/backlog/**` are denied and the
        refusal names `rack transition` plus its export-only kill switch, while
        `tasks/<ID>/plan.md` and files outside the tracker stay writable
why:    `rule_backlog_discipline` §1 has no automation behind it — a hand-edited
        task.yaml desynchronises the FSM, its locks and its audit trail, and the
        rule text alone has never stopped it
"""

from __future__ import annotations

import subprocess

import pytest

from _helpers.hook_chain import build_session_settings, run_tool_chain

pytestmark = pytest.mark.integration

TRACKER = ".agent/ai-hats/tracker/backlog"


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Edit/Write hook chain."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("backlog-gate-home"))

    project = tmp_path_factory.mktemp("backlog-gate-proj")
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
    (project / TRACKER / "tasks" / "HATS-1").mkdir(parents=True, exist_ok=True)
    return project, env, build_session_settings(project)


def _write(project, env, settings, relpath: str):
    return run_tool_chain(
        project,
        "Write",
        {"file_path": str(project / relpath), "content": "state: done\n"},
        settings=settings,
        env=env,
    )


def test_writing_a_task_card_by_hand_is_denied(hooked_project):
    """The state file `rack` owns must not be reachable through Write.

    The refusal has to name the way through (HATS-1253 P4) — the sanctioned
    writer, and the export-only switch for the day the tracker itself is broken.
    A deny with nowhere to go sends the agent to blunt instruments instead."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, f"{TRACKER}/tasks/HATS-1/task.yaml")
    assert verdict.denied, f"a hand-written task.yaml must be denied; got {verdict}"
    assert "rack transition" in verdict.reason, f"deny must name the writer; got {verdict}"
    assert "AI_HATS_BACKLOG_GATE_OFF" in verdict.reason, (
        f"deny must name the kill switch; got {verdict}"
    )
    assert "AI_HATS_BACKLOG_GATE_OFF=1 <command>" not in verdict.reason, (
        "the switch is export-only — a per-call form is a guard the agent turns off"
    )


def test_plan_md_stays_writable(hooked_project):
    """The carve-out of `rule_backlog_discipline` §1b — the plan is authored by
    the agent, not minted by the FSM. Green before the gate exists and after."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, f"{TRACKER}/tasks/HATS-1/plan.md")
    assert not verdict.gated, f"plan.md is the sanctioned deliverable; got {verdict}"


def test_a_file_outside_the_tracker_is_untouched(hooked_project):
    """Same extension, different location — the gate keys on where, not what."""
    project, env, settings = hooked_project
    verdict = _write(project, env, settings, "src/app/config.yaml")
    assert not verdict.gated, f"a file outside the tracker must pass; got {verdict}"
