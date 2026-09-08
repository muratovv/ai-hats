"""e2e (HATS-1709)

flow:   a maintainer runs the long check tier in the background and needs the
        verdict to be the RUNNER's, not the wrapper's — the harness announces a
        backgrounded command by its last element, so a trailing capture turns a
        red run into "exit code 0"
cmds:
    timeout 60 bash packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate/bin/runcheck.sh --log /tmp/e2e.log -- bash scripts/gates.sh unit
expect: the wrapper exits with the runner's own status and writes that same
        number to <log>.rc, so the completion notice and the file agree
why:    two measured incidents announced `exit code 0` over a red tier; the form
        that captures the status is also the form that masks it, and only a
        wrapper puts the capture inside and the status outside
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNCHECK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills"
    / "quality-gate/bin/runcheck.sh"
)


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, path from the repo tree
        ["bash", str(RUNCHECK), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
    )


@pytest.mark.integration
def test_the_runners_status_is_the_wrappers_status(tmp_path):
    log = tmp_path / "run.log"
    res = _run("--log", str(log), "--", "bash", "-c", "exit 7")
    assert res.returncode == 7, res.stderr
    assert (tmp_path / "run.log.rc").read_text().strip() == "7"


@pytest.mark.integration
def test_a_previous_runs_verdict_does_not_survive(tmp_path):
    """The failure mode a hand-rolled form cannot close: no `rm -f`, and a green
    file from last time is read as today's verdict."""
    log = tmp_path / "run.log"
    (tmp_path / "run.log.rc").write_text("0\n")
    log.write_text("everything passed, last week\n")

    res = _run("--log", str(log), "--", "bash", "-c", "exit 7")

    assert res.returncode == 7, res.stderr
    assert (tmp_path / "run.log.rc").read_text().strip() == "7"
    assert "last week" not in log.read_text()


@pytest.mark.integration
def test_a_killed_run_leaves_no_verdict_rather_than_the_old_one(tmp_path):
    """Where clearing the file up front is load-bearing: the run never reaches
    its own write, so anything left behind is last run's. Absent is honest."""
    log = tmp_path / "run.log"
    rc_file = tmp_path / "run.log.rc"
    rc_file.write_text("0\n")  # last week's green, the false verdict on offer

    res = subprocess.run(  # noqa: S603 - fixed argv
        ["timeout", "1", "bash", str(RUNCHECK), "--log", str(log), "--", "sleep", "30"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 124, res.stderr
    assert not rc_file.exists(), f"stale verdict survived: {rc_file.read_text()!r}"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (["--", "true"], "--log is required"),
        (["--log", "L", "true"], "missing --"),
        (["--log", "L", "--"], "no command after --"),
        (["--log"], "--log needs a path"),
    ],
)
def test_an_unusable_invocation_is_refused_not_guessed(tmp_path, args, reason):
    args = [str(tmp_path / "run.log") if a == "L" else a for a in args]
    res = _run(*args)
    assert res.returncode == 2, res.stdout
    assert reason in res.stderr


@pytest.mark.integration
def test_no_shell_stands_between_the_wrapper_and_the_command(tmp_path):
    """A pipe cannot enter, so pipe masking is gone by construction: the shell
    metacharacters arrive as ARGUMENTS, and the command's own status stands."""
    log = tmp_path / "run.log"
    res = _run("--log", str(log), "--", "echo", "a | tail; echo done")
    assert res.returncode == 0, res.stderr
    assert log.read_text().strip() == "a | tail; echo done"
