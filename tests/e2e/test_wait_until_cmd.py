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

import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.wait import parse_happened

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TOUCH_DELAY = 1.0


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
