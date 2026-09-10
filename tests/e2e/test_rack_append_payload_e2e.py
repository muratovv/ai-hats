"""e2e (HATS-1299)

flow:   a developer appending JSON array payloads to task card fields using the CLI
cmds:
    rack transition HATS-9001 --append 'tags=["one","two"]'
expect: array elements are appended to the field in task.yaml and the task card remains
        parseable and addressable by CLI commands
why:    malformed array appending corrupts task YAML structure and makes task cards
        unreadable by tracker commands
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


def test_append_array_adds_entries_and_the_card_stays_addressable(tmp_path):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    created = _rack(tasks_dir, "create", "probe", "--id", "HATS-9001", "--tag", "alpha")
    assert created.returncode == 0, created.stderr

    appended = _rack(tasks_dir, "transition", "HATS-9001", "--append", 'tags=["one","two"]')
    assert appended.returncode == 0, appended.stderr

    read = _rack(tasks_dir, "context", "HATS-9001", "--json")

    assert read.returncode == 0, (
        "the card must still be addressable after --append with a JSON array — "
        f"this is the HATS-1299 defect.\nstdout: {read.stdout[-500:]}\nstderr: {read.stderr[-500:]}"
    )
    assert json.loads(read.stdout)["task"]["tags"] == ["alpha", "one", "two"]


def test_a_write_that_would_not_load_back_is_refused_before_disk(tmp_path):
    """The gate's own proof at process level: the card on disk is untouched."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    _rack(tasks_dir, "create", "probe", "--id", "HATS-9002", "--tag", "alpha")
    card = tasks_dir / "HATS-9002" / "task.yaml"
    before = card.read_text(encoding="utf-8")

    refused = _rack(tasks_dir, "transition", "HATS-9002", "--append", "priority=high")

    assert refused.returncode == 1, refused.stdout
    assert card.read_text(encoding="utf-8") == before
