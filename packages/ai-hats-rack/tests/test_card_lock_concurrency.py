"""Per-card lock pin (HATS-1264, heir of the retired tracker-interop test):
N concurrent writers appending to ONE card must all land — the read-modify-write
of ``<catalog>/<ID>/task.yaml`` is serialized by ``<catalog>/<ID>/.lock``.

Sibling of ``test_alloc_concurrency.py`` (which pins the *other* lock: `.alloc.lock`
handing out unique ids). Same idiom — real OS processes released by a gate file.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 — the test IS about cross-process behaviour
import sys
import time
from pathlib import Path

import yaml

SRC = Path(__file__).resolve().parent.parent / "src"

N_PROCS = 4
N_PER_PROC = 25
CARD = "T-001"

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
    kernel.log_work("T-001", f"{tag}-{i}", actor=f"session:{tag}")
"""


def test_concurrent_appends_to_one_card_all_survive(tmp_path):
    from ai_hats_rack import Kernel

    tasks_dir = tmp_path / "tasks"
    kernel = Kernel(tasks_dir, prefix="T")
    assert kernel.create(actor="test", caller_cwd=tmp_path, title="contended").task.id == CARD

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

    tags = [f"w{k}" for k in range(N_PROCS)]
    procs = [spawn(tag) for tag in tags]
    time.sleep(0.3)  # every racer up and polling before the gun fires
    (tmp_path / "go").write_text("")

    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, f"worker failed:\n{err}"

    raw = yaml.safe_load((tasks_dir / CARD / "task.yaml").read_text())
    messages = [e["message"] for e in raw["work_log"]]
    expected = {f"[session:{tag}] {tag}-{i}" for tag in tags for i in range(N_PER_PROC)}
    # no lost updates: every writer's every append is still on the card
    assert len(messages) == N_PROCS * N_PER_PROC, f"lost {len(expected) - len(messages)} appends"
    assert set(messages) == expected
    # and none got written twice by a racing read-modify-write
    assert len(set(messages)) == len(messages)
