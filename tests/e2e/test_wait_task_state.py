"""e2e (HATS-986)

flow:   a background sub-agent waiting for a task card to reach a target state
cmds:
    ai-hats wait --task HATS-1 --until review --until done --poll 0.2
expect: the process polls until the task card transitions to any specified target
        state and exits with code 0
why:    task state waiting enables non-blocking coordination between background
        sub-agents and parent processes
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.wait import parse_happened

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    return {"PYTHONPATH": checkout_pythonpath(_REPO_ROOT)}


def _card_path(tmp_project, task_id: str) -> Path:
    return tmp_project.agent_dir / "tracker" / "backlog" / "tasks" / task_id / "task.yaml"


def _write_card(tmp_project, task_id: str, state: str) -> Path:
    card = _card_path(tmp_project, task_id)
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(f"id: {task_id}\ntitle: fixture\nstate: {state}\n")
    return card


def _flip_state_after(card: Path, state: str, delay: float) -> subprocess.Popen:
    # os.replace mirrors how rack itself persists a card (models.py atomic_write_text),
    # so the waiter can never observe a torn read here either.
    code = (
        f"import os,time,pathlib; time.sleep({delay}); "
        f"p=pathlib.Path({str(card)!r}); t=p.with_suffix('.tmp'); "
        f"t.write_text('id: HATS-1\\ntitle: fixture\\nstate: {state}\\n'); "
        f"os.replace(t,p)"
    )
    return subprocess.Popen([sys.executable, "-c", code])


# The flipper's clock starts when it is SPAWNED; `wait`'s starts once its
# interpreter has booted, so the delay has to outlast that boot or the card is
# already in the target state at the first poll and the test reads a wait that
# never waited. Boot is ~0.2s here and exceeded 1.0s on a loaded CI runner,
# which is what made these three green locally and red there.
_FLIP_DELAY = 5.0


def test_waits_until_card_reaches_state(tmp_project) -> None:
    """Exit 0 must be CAUSED by observing the flip, not merely coincide with it.

    Asserting only the exit code passes against a ``wait`` whose polling loop is
    deleted, so this pins the observables that separate the two: more than one
    poll, elapsed covering the flip delay, and the card actually in the state.
    """
    card = _write_card(tmp_project, "HATS-1", "execute")
    flipper = _flip_state_after(card, "review", _FLIP_DELAY)
    try:
        result = tmp_project.run(
            "wait",
            "--task",
            "HATS-1",
            "--until",
            "review",
            "--poll",
            "0.2",
            "--timeout",
            "30",
            timeout=60.0,
            extra_env=_env(),
        ).expect_ok()
    finally:
        flipper.wait(timeout=10)

    happened = parse_happened(result.stdout)
    assert happened.polls >= 2, (
        f"exited after {happened.polls} poll(s) — the predicate was false at "
        f"t=0, so a single poll means it never waited"
    )
    # duration_s, not the wait's own elapsed: wait starts its clock after the
    # interpreter boots, while the flipper's delay runs from before that — the
    # two are different clocks and comparing them under-reads by the boot time.
    assert result.duration_s >= _FLIP_DELAY, (
        f"returned in {result.duration_s:.1f}s (wait reported {happened.elapsed_s}s) "
        f"but the card only flips at {_FLIP_DELAY}s"
    )
    assert "state: review" in card.read_text(), "the state the wait claimed to observe"


@pytest.mark.parametrize(
    ("lands_on", "position"),
    [("execute", "first"), ("done", "last")],
)
def test_until_is_repeatable_and_or_combined(tmp_project, lands_on: str, position: str) -> None:
    """The ping-pong hole: a waiter naming one state hangs when the card leaves
    by the other edge. Worker waits for `execute` (rework) OR `done` (accepted).

    Parametrized over BOTH targets (HATS-1493): landing only on `done` — the
    last ``--until`` — leaves "only the last value is honoured" alive, which is
    the very defect the OR-combination exists to prevent.
    """
    card = _write_card(tmp_project, "HATS-1", "review")
    flipper = _flip_state_after(card, lands_on, _FLIP_DELAY)
    try:
        result = tmp_project.run(
            "wait",
            "--task",
            "HATS-1",
            "--until",
            "execute",
            "--until",
            "done",
            "--poll",
            "0.2",
            "--timeout",
            "30",
            timeout=60.0,
            extra_env=_env(),
        ).expect_ok()
    finally:
        flipper.wait(timeout=10)

    happened = parse_happened(result.stdout)
    assert happened.polls >= 2, f"{position} --until: {happened.polls} poll(s), it never waited"
    assert result.duration_s >= _FLIP_DELAY, (
        f"{position} --until: returned in {result.duration_s:.1f}s, before the {_FLIP_DELAY}s flip"
    )
    assert f"state: {lands_on}" in card.read_text()


def test_unknown_task_exits_2_rather_than_waiting(tmp_project) -> None:
    result = tmp_project.run(
        "wait",
        "--task",
        "HATS-99999",
        "--until",
        "done",
        "--poll",
        "0.2",
        "--timeout",
        "5",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 2, (
        f"expected exit 2 for an unknown card, got {result.exit_code} "
        f"(124 would mean a typo'd id silently waits out the clock)"
    )


@pytest.mark.parametrize(
    "args",
    [
        ("--task", "HATS-1"),
        ("--until", "done"),
        ("--until-cmd", "true", "--task", "HATS-1", "--until", "done"),
        (),
    ],
    ids=["task-without-until", "until-without-task", "both-sources", "no-source"],
)
def test_incoherent_predicate_options_are_refused(tmp_project, args: tuple[str, ...]) -> None:
    _write_card(tmp_project, "HATS-1", "execute")

    result = tmp_project.run("wait", *args, timeout=30.0, extra_env=_env())

    assert result.exit_code == 2, f"expected usage refusal, got {result.exit_code}"


def test_wait_task_honors_ai_hats_dir_sandbox(tmp_project) -> None:
    """Card exists ONLY in sandbox directory, AI_HATS_DIR points to sandbox.
    ai-hats wait from a project directory finds card in sandbox (HATS-1471).
    """
    sbx_agent = tmp_project.path / "sandbox" / ".agent" / "ai-hats"
    sbx_card = sbx_agent / "tracker" / "backlog" / "tasks" / "HATS-777" / "task.yaml"
    sbx_card.parent.mkdir(parents=True)
    sbx_card.write_text("id: HATS-777\ntitle: sandbox card\nstate: done\n")

    env = dict(_env())
    env["AI_HATS_DIR"] = str(sbx_agent)

    tmp_project.run(
        "wait",
        "--task",
        "HATS-777",
        "--until",
        "done",
        "--poll",
        "0.2",
        "--timeout",
        "5",
        timeout=10.0,
        extra_env=env,
    ).expect_ok()
