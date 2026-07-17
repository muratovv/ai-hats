"""Two-process id-allocation pin (HATS-936 heir): concurrent creators must
never cross-write one id — alloc+reserve is atomic under `.alloc.lock`."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 — the test IS about cross-process behaviour
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

N_PER_PROC = 15

WORKER = """
import sys, time
from pathlib import Path
from ai_hats_rack import Kernel

tasks_dir = Path(sys.argv[1])
tag = sys.argv[2]
n = int(sys.argv[3])
go = tasks_dir.parent / "go"
while not go.exists():
    time.sleep(0.005)
kernel = Kernel(tasks_dir, prefix="T")
for i in range(n):
    result = kernel.create(actor="test", caller_cwd=Path.cwd(), title=f"{tag}-{i}")
    print(result.task.id, flush=True)
"""


def test_two_process_create_yields_unique_ids(tmp_path):
    tasks_dir = tmp_path / "tasks"
    env = dict(os.environ, PYTHONPATH=str(SRC))

    def spawn(tag: str) -> subprocess.Popen:
        return subprocess.Popen(  # noqa: S603 — fixed argv, test-controlled
            [sys.executable, "-c", WORKER, str(tasks_dir), tag, str(N_PER_PROC)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )

    procs = [spawn("a"), spawn("b")]
    time.sleep(0.2)  # both racers up and polling before the gun fires
    (tmp_path / "go").write_text("")

    ids: list[str] = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"worker failed:\n{err}"
        ids.extend(out.split())

    assert len(ids) == 2 * N_PER_PROC
    assert len(set(ids)) == 2 * N_PER_PROC, f"id collision: {sorted(ids)}"
    # every allocated id has exactly one card dir on disk
    on_disk = sorted(p.name for p in tasks_dir.iterdir() if p.is_dir())
    assert on_disk == sorted(ids)
