"""e2e: stress test race condition on rack work_log and task card updates (HATS-1466).

Validates that N parallel `rack transition --log` writing to a single
task card in tight millisecond windows lose no entries and produce no YAML corruption.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
_N = 50


def test_rack_parallel_log_writes_single_card(tmp_project) -> None:
    from _helpers.env import checkout_pythonpath

    env = {**os.environ, **tmp_project.env}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)

    # 1. Create a single card
    create_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_hats_rack",
            "create",
            "race-target-task",
            "--description",
            "target card for race condition test",
        ],
        cwd=str(tmp_project.path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert create_proc.returncode == 0, f"rack create failed: {create_proc.stderr}"

    card_id = None
    for line in create_proc.stdout.splitlines():
        if line.strip().startswith("Created:"):
            card_id = line.split()[1]
            break
    assert card_id is not None, f"Failed to get card_id from stdout: {create_proc.stdout}"

    # 2. Launch _N parallel transition --log calls in tight window
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_hats_rack",
                "transition",
                card_id,
                "--log",
                f"concurrent log entry payload {i:03d}",
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
        assert rc == 0, f"rack transition --log failed ({rc}):\n{stdout}\n{stderr}"

    # 3. Read the task card and verify work_log entries
    card_file = (
        tmp_project.path
        / ".agent"
        / "ai-hats"
        / "tracker"
        / "backlog"
        / "tasks"
        / card_id
        / "task.yaml"
    )
    assert card_file.is_file(), f"Card file {card_file} missing"

    content = card_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert parsed is not None, "Failed to parse task.yaml"

    work_log = parsed.get("work_log", [])
    log_messages = [
        item.get("message", "") if isinstance(item, dict) else str(item) for item in work_log
    ]

    matching_entries = [msg for msg in log_messages if "concurrent log entry payload" in msg]
    assert len(matching_entries) == _N, (
        f"Race condition lost updates! Expected {_N} entries, got {len(matching_entries)}. "
        f"All messages: {log_messages}"
    )

    # 4. Verify backlog integrity with `rack doctor`
    doc_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_hats_rack",
            "doctor",
        ],
        cwd=str(tmp_project.path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert (
        doc_proc.returncode == 0
    ), f"rack doctor failed after stress test:\n{doc_proc.stdout}\n{doc_proc.stderr}"
