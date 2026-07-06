"""e2e: concurrent ``ai-hats task create`` never collide on a task id (HATS-936).

Real launcher + real binary per ``dev_rule_e2e_gate`` (the fix touches
``src/ai_hats/cli/task.py``). N ``task create`` processes are launched at once
against one project; the alloc lock must hand each a DISTINCT id with an intact
card. Under the pre-HATS-936 read-max-then-write allocation these racers share
an id and cross-write one card — this test goes RED against that revert.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

_N = 8
_TASKS_REL = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"


def _created_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        # "Created: HATS-042 — title [brainstorm] (medium)"
        if line.startswith("Created:"):
            return line.split()[1]
    return None


def test_parallel_task_create_allocates_distinct_ids(tmp_project) -> None:
    binary = str(tmp_project.ai_hats_binary)
    env = {**os.environ, **tmp_project.env}

    # Launch all N at once (non-blocking) so their allocations overlap.
    procs = [
        subprocess.Popen(
            [binary, "task", "create", f"race-{i}", "--description", f"body {i}"],
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
        assert rc == 0, f"task create failed ({rc}):\n{stdout}\n{stderr}"

    ids = [_created_id(o[1]) for o in outs]
    assert all(ids), f"could not parse every Created id: {ids}"
    assert len(set(ids)) == _N, f"id collision — allocation raced: {sorted(ids)}"

    # Every id is a real card on disk (no cross-write left a card missing).
    tasks_dir = tmp_project.path / _TASKS_REL
    for tid in ids:
        assert (tasks_dir / tid / "task.yaml").is_file(), f"card {tid} missing on disk"
