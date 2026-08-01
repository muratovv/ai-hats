"""Real-SIGINT coverage for the finalize-window shield — HATS-1426.

The unit suite calls the installed handler directly, which proves the counting
but not that the handler is the one the kernel actually reaches. Only a real
signal to a real process proves installation, and only that catches a revert of
the ``sigint_shield`` wiring. Kept out of the default run by the
``integration`` marker.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

CHILD = """
import sys, time
from ai_hats.runtime import FinalizeAborted, sigint_shield

ready, marker = sys.argv[1], sys.argv[2]
try:
    with sigint_shield():
        open(ready, "w").write("ready")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            time.sleep(0.02)
        open(marker, "w").write("finalized")
except FinalizeAborted:
    sys.exit(130)
"""


def _spawn(tmp_path):
    ready, marker = tmp_path / "ready", tmp_path / "marker"
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", CHILD, str(ready), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists():
        time.sleep(0.02)
    if not ready.exists():
        proc.kill()
        pytest.fail("child never entered the shielded window")
    return proc, marker


@pytest.mark.integration
def test_single_real_sigint_does_not_kill_the_shielded_body(tmp_path):
    # GIVEN a process inside the shielded finalize window
    proc, marker = _spawn(tmp_path)

    # WHEN one real SIGINT arrives (the HATS-1426 incident)
    os.kill(proc.pid, signal.SIGINT)
    _, stderr = proc.communicate(timeout=30)

    # THEN the window ran to completion instead of dying mid-step
    assert proc.returncode == 0
    assert marker.read_text() == "finalized"
    assert "press Ctrl-C 3" in stderr


@pytest.mark.integration
def test_three_rapid_real_sigints_abort_with_130(tmp_path):
    # GIVEN the same shielded window
    proc, marker = _spawn(tmp_path)

    # WHEN the operator insists — three presses inside the 1.5s window
    for _ in range(3):
        os.kill(proc.pid, signal.SIGINT)
        time.sleep(0.05)
    _, stderr = proc.communicate(timeout=30)

    # THEN the escape hatch fired: canonical 130, body abandoned
    assert proc.returncode == 130
    assert not marker.exists()
    assert "finalize aborted by operator" in stderr
