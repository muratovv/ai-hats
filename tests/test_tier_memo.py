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
import os


def test_green():
    assert True


def test_also_green():
    assert True


def test_red():
    assert False
"""

#: A test that spawns pytest again — the hermetic unit gate does exactly this,
#: twice, and asserts the second run refuses.
NESTED = """
import os
from pathlib import Path


def test_reports_what_a_child_would_inherit():
    Path("inherited.txt").write_text(str(os.environ.get("AI_HATS_GATE_TIER_MEMO")), encoding="utf-8")
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


def test_the_memo_does_not_reach_what_a_test_spawns(probe: Path):
    """The defect this consumption exists to prevent, and it cost a green stage:
    the hermetic unit gate runs `gates.sh unit` twice and asserts both refuse.
    With the variable inherited, the first nested run wrote its own node id into
    the gate's memo and the second deselected it — the run exited 0 and the gate
    test failed for a reason that had nothing to do with the gate."""
    memo = probe / "memo"
    (probe / "test_nested.py").write_text(NESTED, encoding="utf-8")

    _run(probe, memo, "test_nested.py")

    assert (probe / "inherited.txt").read_text(encoding="utf-8") == "None"


def test_the_workers_of_a_parallel_run_deselect_what_the_controller_consumed(probe: Path):
    """The variable is taken out of the environment before a worker exists, so a
    worker gets the path by `pytest_configure_node` or not at all — and a worker
    that deselected differently from its siblings would make xdist refuse the run
    outright ("Different tests were collected")."""
    pytest.importorskip("xdist")
    memo = probe / "memo"
    _run(probe, memo, "-n", "2", "test_probe.py::test_green")

    second = _run(
        probe, memo, "-n", "2", "test_probe.py::test_green", "test_probe.py::test_also_green"
    )

    assert second.returncode == 0, second.stdout + second.stderr
    # Two named, one run: xdist reports the deselection as a smaller count rather
    # than as a `deselected` line, so the count is what says it happened.
    assert "1 passed" in second.stdout, second.stdout
    assert sorted(memo.read_text(encoding="utf-8").split()) == [
        "test_probe.py::test_also_green",
        "test_probe.py::test_green",
    ]


def test_a_parallel_stage_whose_every_test_is_already_green_still_passes(probe: Path):
    """The serial case above, under xdist — where the deselecting happens in the
    workers and the exit code is the controller's. A controller counting only
    its own deselections sees none, leaves the 5 standing, and the gate reads a
    fully-proven zone as a red one."""
    pytest.importorskip("xdist")
    memo = probe / "memo"
    _run(probe, memo, "-n", "2", "test_probe.py::test_green")

    second = _run(probe, memo, "-n", "2", "test_probe.py::test_green")

    assert second.returncode == 0, second.stdout + second.stderr


def test_without_the_gate_there_is_no_memo_at_all(probe: Path):
    """The negative control: the hooks are inert unless a gate names the file,
    so an ordinary `pytest` run neither reads nor writes one."""
    first = _run(probe, None, "test_probe.py::test_green")
    second = _run(probe, None, "test_probe.py::test_green")

    assert "1 passed" in first.stdout, first.stdout
    assert "1 passed" in second.stdout, second.stdout
    assert not list(probe.glob("memo*"))
