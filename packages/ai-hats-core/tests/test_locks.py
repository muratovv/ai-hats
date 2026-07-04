"""Tests for the cross-process RMW lock (HATS-526).

Contract: ``locked_path`` serializes a read-modify-write section across
processes (no lost updates), and a held lock surfaces as a friendly
``LockTimeoutError`` instead of an indefinite hang.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_core import locks

_RMW_SNIPPET = """
import sys, time
from pathlib import Path
from ai_hats_core.locks import locked_path

counter = Path(sys.argv[1])
with locked_path(counter):
    value = int(counter.read_text())
    time.sleep(0.05)  # widen the race window: unlocked RMW reliably loses updates
    counter.write_text(str(value + 1))
"""


def test_parallel_rmw_loses_no_updates(tmp_path: Path) -> None:
    counter = tmp_path / "counter.txt"
    counter.write_text("0")

    env = os.environ.copy()
    core_src = str(Path(locks.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = core_src + os.pathsep + env.get("PYTHONPATH", "")

    procs = [
        subprocess.Popen([sys.executable, "-c", _RMW_SNIPPET, str(counter)], env=env)
        for _ in range(4)
    ]
    assert [p.wait(timeout=30) for p in procs] == [0, 0, 0, 0]
    assert counter.read_text() == "4"


def test_timeout_raises_friendly_error(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"

    with locks.locked_path(target):
        with pytest.raises(locks.LockTimeoutError) as exc_info:
            with locks.locked_path(target, timeout=0.1):
                pytest.fail("lock must not be acquirable while held")

    message = str(exc_info.value)
    assert str(target) in message
    assert f"{target}.lock" in message


def test_released_lock_is_reacquirable(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    with locks.locked_path(target):
        pass
    with locks.locked_path(target, timeout=0.5):
        pass
