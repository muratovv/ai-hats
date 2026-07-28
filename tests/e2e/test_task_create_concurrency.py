"""e2e: concurrent ``rack create`` never collide on a task id (HATS-936).

Re-pointed off the legacy ``ai-hats task`` CLI (HATS-1263): the allocator under
test is ``ai_hats_rack.kernel.Kernel._next_id`` behind its alloc lock. N
``create`` processes launch at once against one project; the lock must hand each
a DISTINCT id with an intact card. Pre-HATS-936 read-max-then-write allocation
makes the racers share an id and cross-write one card — RED against that revert.
``python -m ai_hats_rack`` + an explicit PYTHONPATH pins the CURRENT checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

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
