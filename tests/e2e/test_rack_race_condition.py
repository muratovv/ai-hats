"""e2e (HATS-1466)

flow:   several agents log work against the SAME card at once — parallel
        sub-agents on one ticket, or a session racing its own hooks
cmds:
    rack create race-target-task --description "..."
    rack transition <ID> --log "<message>"
    rack doctor
expect: every entry survives — the card holds exactly as many work_log lines as
        calls made, the YAML still parses, and `rack doctor` reports the backlog
        intact
why:    without the card lock a losing writer's read-modify-write drops the
        winner's entry, or leaves half-serialised YAML — both invisible until
        someone looks for a log line that was never there. The Kernel-API tier
        is covered by test_card_lock_concurrency.py (HATS-1264); this drives
        the CLI, the surface agents actually call.
"""  # comment-length: allow — the flow block IS the docstring (gen_e2e_catalog)

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.rack]

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
    assert doc_proc.returncode == 0, (
        f"rack doctor failed after stress test:\n{doc_proc.stdout}\n{doc_proc.stderr}"
    )


def test_rack_parallel_workers_multiple_messages_each(tmp_project) -> None:
    """Simulates 5 parallel worker processes each making 10 log entries (50 total)."""
    from _helpers.env import checkout_pythonpath

    env = {**os.environ, **tmp_project.env}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)

    create_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_hats_rack",
            "create",
            "multi-worker-task",
            "--description",
            "target card for multi-worker race test",
        ],
        cwd=str(tmp_project.path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert create_proc.returncode == 0, f"create failed: {create_proc.stderr}"

    card_id = None
    for line in create_proc.stdout.splitlines():
        if line.strip().startswith("Created:"):
            card_id = line.split()[1]
            break
    assert card_id is not None

    num_workers = 5
    msgs_per_worker = 10

    def _worker_job(worker_id: int) -> list[int]:
        codes = []
        for m in range(msgs_per_worker):
            res = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ai_hats_rack",
                    "transition",
                    card_id,
                    "--log",
                    f"worker-{worker_id} msg-{m:02d}",
                ],
                cwd=str(tmp_project.path),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            codes.append(res.returncode)
        return codes

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(_worker_job, w) for w in range(num_workers)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    for worker_codes in results:
        for code in worker_codes:
            assert code == 0

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
    content = card_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert parsed is not None

    work_log = parsed.get("work_log", [])
    log_messages = [
        item.get("message", "") if isinstance(item, dict) else str(item) for item in work_log
    ]
    matching = [msg for msg in log_messages if "worker-" in msg]
    assert len(matching) == num_workers * msgs_per_worker, (
        f"Expected {num_workers * msgs_per_worker} entries, got {len(matching)}"
    )
