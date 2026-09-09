"""A test the gate has already seen green on this tree does not run again.

Zones overlap on purpose — a consent test on the codex surface asserts both — so
one gate run can name two stages that share tests. They are the same tests on the
same tree, and the supervisor's rule is that such a test executes exactly once
per sha; the second stage is told it need not.

The plugin under test is the repo's own root `conftest.py`, loaded here BY NAME
into a scratch run (`-p conftest`, the repo root on `PYTHONPATH`). A copy of its
hooks in this file would test the copy — and the memo's one dangerous mistake,
recording a failure, would be exactly as easy to make in both.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMO_ENV = "AI_HATS_GATE_TIER_MEMO"

PROBE = """
def test_green():
    assert True


def test_also_green():
    assert True


def test_red():
    assert False
"""


@pytest.fixture()
def probe(tmp_path: Path) -> Path:
    """A scratch suite of two passing tests and one failing one."""
    (tmp_path / "test_probe.py").write_text(PROBE, encoding="utf-8")
    return tmp_path


def _run(probe: Path, memo: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(probe),
        "PYTHONPATH": str(REPO_ROOT),
        # Randomisation would reorder the two greens; this file asserts on which
        # ones were recorded, never on their order.
        "PYTEST_ADDOPTS": "",
    }
    if memo is not None:
        env[MEMO_ENV] = str(memo)
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "pytest", "-p", "conftest", "-p", "no:randomly", "-q", *args],
        cwd=probe,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_only_a_passing_test_is_written_into_the_memo(probe: Path):
    """The one way this could lie: a red left in the memo lets the next stage
    report green for a test it never ran."""
    memo = probe / "memo"

    _run(probe, memo)

    recorded = memo.read_text(encoding="utf-8").split()
    assert recorded == ["test_probe.py::test_green", "test_probe.py::test_also_green"], recorded


def test_the_second_run_does_not_re_run_what_the_first_proved(probe: Path):
    """The two runs stand for two stages of one gate run, sharing a tree."""
    memo = probe / "memo"
    _run(probe, memo, "test_probe.py::test_green")

    second = _run(probe, memo, "test_probe.py::test_green", "test_probe.py::test_also_green")

    assert "1 deselected" in second.stdout, second.stdout
    assert "1 passed" in second.stdout, second.stdout


def test_a_stage_whose_every_test_is_already_green_still_passes(probe: Path):
    """pytest spells "collected nothing" 5, and a stage exiting 5 is a red gate.
    Here nothing was collected BECAUSE everything was proven on this tree."""
    memo = probe / "memo"
    _run(probe, memo, "test_probe.py::test_green")

    second = _run(probe, memo, "test_probe.py::test_green")

    assert second.returncode == 0, second.stdout + second.stderr
    assert "1 deselected" in second.stdout, second.stdout


def test_collection_answers_for_the_whole_set_however_much_has_run(probe: Path):
    """`tests/test_e2e_zone_partition.py` asks each stage what it WOULD run, and
    the laws it checks are set equalities. A memo answering there would shrink
    the parts and the union with them — the tier could lose a test and every law
    would still hold."""
    memo = probe / "memo"
    _run(probe, memo, "test_probe.py::test_green")

    collected = _run(probe, memo, "--collect-only")

    assert "test_probe.py::test_green" in collected.stdout, collected.stdout
    assert "deselected" not in collected.stdout, collected.stdout


def test_without_the_gate_there_is_no_memo_at_all(probe: Path):
    """The negative control: the hooks are inert unless a gate names the file,
    so an ordinary `pytest` run neither reads nor writes one."""
    first = _run(probe, None, "test_probe.py::test_green")
    second = _run(probe, None, "test_probe.py::test_green")

    assert "1 passed" in first.stdout, first.stdout
    assert "1 passed" in second.stdout, second.stdout
    assert not list(probe.glob("memo*"))
