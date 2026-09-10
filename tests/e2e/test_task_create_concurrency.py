"""e2e (HATS-936, HATS-1263)

flow:   multiple agent processes creating tasks concurrently in the same project
cmds:
    rack create "race task"
expect: each concurrent creation command receives a unique task ID and creates
        a complete card directory without file collisions
why:    task ID allocation must be synchronized across processes to prevent duplicate
        IDs and corrupted task cards
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parents[2]
_N = 8
_TASKS_REL = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"


def _created_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        # "Created: HATS-042 [brainstorm] title"
        if line.startswith("Created:"):
            return line.split()[1]
    return None


def test_parallel_task_create_allocates_distinct_ids(tmp_project) -> None:
    from _helpers.env import checkout_pythonpath

    env = {**os.environ, **tmp_project.env}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)

    # Launch all N at once (non-blocking) so their allocations overlap.
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_hats_rack",
                "create",
                f"race-{i}",
                "--description",
                f"body {i}",
            ],
            cwd=str(tmp_project.path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(_N)
    ]
    outs = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=60)
        outs.append((p.returncode, stdout, stderr))

    for rc, stdout, stderr in outs:
        assert rc == 0, f"rack create failed ({rc}):\n{stdout}\n{stderr}"

    ids = [_created_id(o[1]) for o in outs]
    assert all(ids), f"could not parse every Created id: {ids}"
    assert len(set(ids)) == _N, f"id collision — allocation raced: {sorted(ids)}"

    # Every id is a real card on disk (no cross-write left a card missing).
    tasks_dir = tmp_project.path / _TASKS_REL
    for tid in ids:
        assert (tasks_dir / tid / "task.yaml").is_file(), f"card {tid} missing on disk"
