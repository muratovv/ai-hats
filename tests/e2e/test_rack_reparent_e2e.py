"""e2e (HATS-1350)

flow:   a developer modifying task parent relationships using link flags
cmds:
    rack transition HATS-101 --unlink parent_task:HATS-100 --link parent_task:HATS-102
expect: direct field mutation of parent_task is rejected while atomic unlink and link
        flags update the parent reference in task.yaml
why:    parent_task is a structural relationship field that must be updated through
        graph validation rather than direct field assignment
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _rack(tasks_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Drive the real rack CLI in its own process against THIS checkout."""
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args, "--tasks-dir", str(tasks_dir)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_set_parent_task_is_refused_as_structural_field(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    _rack(tasks_dir, "create", "parent", "--id", "HATS-100")
    _rack(tasks_dir, "create", "child", "--id", "HATS-101")

    res = _rack(tasks_dir, "transition", "HATS-101", "--set", "parent_task=HATS-100")
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "cannot target the structural field 'parent_task'" in combined


def test_link_unparented_card_succeeds(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    _rack(tasks_dir, "create", "parent", "--id", "HATS-100")
    _rack(tasks_dir, "create", "child", "--id", "HATS-101")

    res = _rack(tasks_dir, "transition", "HATS-101", "--link", "parent_task:HATS-100")
    assert res.returncode == 0, res.stderr

    read = _rack(tasks_dir, "context", "HATS-101", "--json")
    assert read.returncode == 0
    payload = json.loads(read.stdout)
    assert payload["task"]["parent_task"] == "HATS-100"


def test_link_already_linked_card_fails(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    _rack(tasks_dir, "create", "parent1", "--id", "HATS-100")
    _rack(tasks_dir, "create", "parent2", "--id", "HATS-102")
    _rack(tasks_dir, "create", "child", "--id", "HATS-101")

    _rack(tasks_dir, "transition", "HATS-101", "--link", "parent_task:HATS-100")

    res = _rack(tasks_dir, "transition", "HATS-101", "--link", "parent_task:HATS-102")
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "already_linked" in combined or "already has 'parent_task' set to 'HATS-100'" in combined


def test_atomic_reparent_via_unlink_and_link_succeeds(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    _rack(tasks_dir, "create", "parent1", "--id", "HATS-100")
    _rack(tasks_dir, "create", "parent2", "--id", "HATS-102")
    _rack(tasks_dir, "create", "child", "--id", "HATS-101")

    _rack(tasks_dir, "transition", "HATS-101", "--link", "parent_task:HATS-100")

    reparent = _rack(
        tasks_dir,
        "transition",
        "HATS-101",
        "--unlink",
        "parent_task:HATS-100",
        "--link",
        "parent_task:HATS-102",
    )
    assert reparent.returncode == 0, f"stderr: {reparent.stderr}, stdout: {reparent.stdout}"

    read = _rack(tasks_dir, "context", "HATS-101", "--json")
    assert read.returncode == 0
    payload = json.loads(read.stdout)
    assert payload["task"]["parent_task"] == "HATS-102"
