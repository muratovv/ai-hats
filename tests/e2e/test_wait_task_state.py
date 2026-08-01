"""e2e for ``ai-hats wait --task ... --until ...`` (HATS-986).

``--until`` is repeatable and OR-combined: a ping-pong waiter that names only
one target state hangs forever when the card leaves by the other edge.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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


def test_waits_until_card_reaches_state(tmp_project) -> None:
    card = _write_card(tmp_project, "HATS-1", "execute")
    flipper = _flip_state_after(card, "review", 1.0)
    try:
        tmp_project.run(
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


def test_until_is_repeatable_and_or_combined(tmp_project) -> None:
    """The ping-pong hole: a waiter naming one state hangs when the card leaves
    by the other edge. Worker waits for `execute` (rework) OR `done` (accepted).
    """
    card = _write_card(tmp_project, "HATS-1", "review")
    flipper = _flip_state_after(card, "done", 1.0)
    try:
        tmp_project.run(
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
