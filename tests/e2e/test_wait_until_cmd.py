"""e2e for ``ai-hats wait --until-cmd`` (HATS-986).

The load-bearing case is the LAST one: a predicate that is broken (exit > 1)
must not read as "not yet". A plain ``until`` shell loop cannot tell those
apart and hangs forever; that indistinguishability is why this command exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    return {"PYTHONPATH": checkout_pythonpath(_REPO_ROOT)}


def _touch_after(path: Path, delay: float) -> subprocess.Popen:
    code = f"import time,pathlib; time.sleep({delay}); pathlib.Path({str(path)!r}).touch()"
    return subprocess.Popen([sys.executable, "-c", code])


def test_predicate_becomes_true_exits_zero(tmp_project) -> None:
    marker = tmp_project.path / "marker"
    toucher = _touch_after(marker, 1.0)
    try:
        tmp_project.run(
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


@pytest.mark.parametrize(
    ("extra_args", "bad_flag"),
    [
        (("--poll", "0", "--timeout", "3"), "--poll"),
        (("--poll", "-5", "--timeout", "3"), "--poll"),
        (("--poll", "nan", "--timeout", "3"), "--poll"),
        (("--poll", "0.2", "--timeout", "-1"), "--timeout"),
        (("--poll", "0.2", "--timeout", "nan"), "--timeout"),
    ],
)
def test_non_positive_poll_or_bad_timeout_rejected_at_input(
    tmp_project, extra_args: tuple[str, ...], bad_flag: str
) -> None:
    """``--poll <= 0`` (or non-finite) and a negative/non-finite ``--timeout``
    must be refused before polling starts (HATS-1452) — else they collapse
    into a busy-loop (``time.sleep(max(nap, 0.0))`` naps for 0s on any of
    these). The predicate is already-true ``true`` in every case: without the
    guard this exits 0 immediately, so a green run here means the guard is
    missing, not that the wait "happened to be fast".
    """
    result = tmp_project.run(
        "wait",
        "--until-cmd",
        "true",
        *extra_args,
        timeout=30.0,
        extra_env=_env(),
    )

    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}: {result.stderr[-300:]}"
    assert result.duration_s < 5.0, (
        f"took {result.duration_s:.1f}s — rejection must happen before polling starts, "
        f"not after a busy-loop"
    )
    assert bad_flag in result.stderr, f"stderr should name {bad_flag}: {result.stderr[-300:]}"


def test_zero_timeout_still_legal(tmp_project) -> None:
    """``--timeout 0`` stays legal ('wait forever') — must not be caught by
    the negative-timeout guard (HATS-1452 regression)."""
    tmp_project.run(
        "wait",
        "--until-cmd",
        "true",
        "--poll",
        "0.2",
        "--timeout",
        "0",
        timeout=30.0,
        extra_env=_env(),
    ).expect_ok()
