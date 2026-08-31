"""e2e (HATS-986)

flow:   a background script waiting for a custom shell command predicate to pass
cmds:
    ai-hats wait --until-cmd "test -f marker" --poll 0.2
expect: the process polls the shell command until it exits 0, timing out with
        code 124 if unmet, or exiting code 2 if broken
why:    command waiting allows processes to block until external conditions are met with
        distinction between timeouts and errors
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _helpers.wait import parse_happened

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Same race as test_wait_task_state._FLIP_DELAY: the toucher's clock starts at
# spawn, `wait`'s only once its interpreter is up, so a delay under that boot
# leaves the marker already there at the first poll.
_TOUCH_DELAY = 5.0


def _env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    return {"PYTHONPATH": checkout_pythonpath(_REPO_ROOT)}


def _touch_after(path: Path, delay: float) -> subprocess.Popen:
    code = f"import time,pathlib; time.sleep({delay}); pathlib.Path({str(path)!r}).touch()"
    return subprocess.Popen([sys.executable, "-c", code])


def test_predicate_becomes_true_exits_zero(tmp_project) -> None:
    """Exit 0 must be CAUSED by the marker appearing, not merely coincide.

    ``expect_ok()`` alone passes against a ``wait`` with its polling loop
    deleted; the poll count and elapsed time are what refuse that.
    """
    marker = tmp_project.path / "marker"
    toucher = _touch_after(marker, _TOUCH_DELAY)
    try:
        result = tmp_project.run(
            "wait",
            "--until-cmd",
            f"test -f {marker}",
            "--poll",
            "0.2",
            "--timeout",
            "30",
            timeout=60.0,
            extra_env=_env(),
        ).expect_ok()
    finally:
        toucher.wait(timeout=10)

    happened = parse_happened(result.stdout)
    assert happened.polls >= 2, (
        f"exited after {happened.polls} poll(s) — the marker is absent at t=0, "
        f"so one poll means it never waited"
    )
    # duration_s, not the wait's own elapsed: wait starts its clock after the
    # interpreter boots, while the toucher's delay runs from before that — the
    # two are different clocks and comparing them under-reads by the boot time.
    assert result.duration_s >= _TOUCH_DELAY, (
        f"returned in {result.duration_s:.1f}s (wait reported {happened.elapsed_s}s) "
        f"but the marker only appears at {_TOUCH_DELAY}s"
    )
    assert marker.exists(), "the event the wait claimed to observe"


def test_predicate_already_true_returns_immediately(tmp_project) -> None:
    """The waiter may start AFTER the event — that must not hang."""
    marker = tmp_project.path / "marker"
    marker.touch()

    result = tmp_project.run(
        "wait",
        "--until-cmd",
        f"test -f {marker}",
        "--poll",
        "30",
        "--timeout",
        "30",
        timeout=60.0,
        extra_env=_env(),
    ).expect_ok()

    assert result.duration_s < 15.0, "returned only after a poll interval, not immediately"


def test_timeout_exits_124(tmp_project) -> None:
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        "test -f /nonexistent/never-appears",
        "--poll",
        "0.2",
        "--timeout",
        "1",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 124, f"expected 124, got {result.exit_code}: {result.stderr[-300:]}"


def test_hung_predicate_still_times_out(tmp_project) -> None:
    """The deadline must bound the WAIT, not merely the gaps between probes.

    Before HATS-1598 the probe ran unbounded and the deadline was read only
    after it returned, so a predicate that never answers made ``--timeout``
    unreachable — 124 was dead code for this whole class.
    """
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        "sleep 300",
        "--poll",
        "0.2",
        "--timeout",
        "3",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 124, f"expected 124, got {result.exit_code}: {result.stderr[-300:]}"
    assert result.duration_s < 30.0, (
        f"gave up only after {result.duration_s:.1f}s — a 3s --timeout that "
        f"returns that late is not bounding the probe"
    )


def test_hung_predicate_breaks_when_probe_budget_expires(tmp_project) -> None:
    """``--timeout 0`` is the case the deadline cannot cover.

    "Wait forever" is a statement about the EVENT, never a licence for one probe
    to hang forever — under it there is no deadline to bound the probe with, so
    ``--probe-timeout`` is the only thing between a dead ssh and a wedged
    session. Asserting the reason, not just exit 2: an unknown flag also exits 2
    (click UsageError), which would pass this test with the feature absent.
    """
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        "sleep 300",
        "--poll",
        "0.2",
        "--timeout",
        "0",
        "--probe-timeout",
        "2",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 2, f"expected 2, got {result.exit_code}: {result.output[-300:]}"
    assert "predicate did not answer in 2s" in result.output, (
        f"exit 2 must name the expired probe budget, else it is indistinguishable "
        f"from a rejected flag: {result.output[-300:]}"
    )


def _alive(pid: int) -> bool:
    """Whether ``pid`` still exists (signal 0 probes without delivering)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_hung_compound_predicate_leaves_no_orphan(tmp_project) -> None:
    """Killing the probe must kill what the probe started.

    ``timeout=`` alone reaps only the direct child: under ``shell=True`` a
    compound predicate keeps its own children, so a "bounded" wait would return
    while the real work — the dead ssh it was waiting on — runs on, reparented
    and invisible. The predicate here publishes its child's pid so the test can
    assert on the process rather than on the wait's exit code.
    """
    pidfile = tmp_project.path / "child.pid"
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        f"sleep 300 & echo $! > {pidfile}; wait",
        "--poll",
        "0.2",
        "--timeout",
        "0",
        "--probe-timeout",
        "2",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 2, f"expected 2, got {result.exit_code}: {result.output[-300:]}"
    child = int(pidfile.read_text().strip())
    try:
        settle = time.monotonic() + 5.0
        while _alive(child) and time.monotonic() < settle:
            time.sleep(0.1)
        assert not _alive(child), (
            f"grandchild {child} outlived the probe that spawned it — the kill "
            f"reached the shell only, so the wait returned over live work"
        )
    finally:
        if _alive(child):
            os.kill(child, signal.SIGKILL)


@pytest.mark.parametrize(
    ("predicate", "label"),
    [
        ("exit 3", "explicit abort code"),
        ("definitely_not_a_command_986", "typo — shell returns 127"),
    ],
)
def test_broken_predicate_exits_2_not_124(tmp_project, predicate: str, label: str) -> None:
    """The load-bearing case: broken must not be swallowed as "not yet".

    Were the >1 branch missing, this would poll to the deadline and exit 124 —
    i.e. a broken predicate would look exactly like an event that never came.
    """
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        predicate,
        "--poll",
        "0.2",
        "--timeout",
        "5",
        timeout=60.0,
        extra_env=_env(),
    )

    assert result.exit_code == 2, (
        f"{label}: expected exit 2, got {result.exit_code} "
        f"(124 would mean the break was swallowed as a timeout)"
    )


# HATS-1452 flag-guard coverage moved to tests/test_cli_wait_flags.py
# (HATS-1493): arg validation precedes polling and needs no process.
