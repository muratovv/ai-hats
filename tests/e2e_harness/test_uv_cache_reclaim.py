"""The e2e harness's uv-cache reclaim at session end.

Subject: ``tests/e2e/_helpers/uv_cache.py``. Every ``uv pip install --reinstall``
from a per-worker clone leaves a build in the shared uv cache under a path key
nobody will ever hit again; ``uv cache prune`` drops the superseded revisions
and a by-name ``uv cache clean`` drops the last one. Neither uv nor the network
is touched here — the runner is injected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _helpers.uv_cache import (
    LOCK_WAIT_S,
    reclaim,
    reclaim_commands,
    reclaims_at_session_end,
    workspace_members,
)


def _pyproject(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[project]\nname = "{name}"\nversion = "0.1"\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _pyproject(tmp_path / "pyproject.toml", "ai-hats")
    _pyproject(tmp_path / "packages" / "core" / "pyproject.toml", "ai-hats-core")
    return tmp_path


class _Runner:
    """A ``subprocess.run`` stand-in: records every call, answers from a script."""

    def __init__(self, *answers: subprocess.CompletedProcess[str] | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, cmd, **kwargs):  # noqa: ANN001, ANN003, ANN204
        self.calls.append({"cmd": cmd, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ok(stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)


def test_workspace_members_are_read_from_the_pyprojects(tmp_path: Path) -> None:
    """Root name first, then every packages/*/pyproject.toml — never a kept list."""
    _pyproject(tmp_path / "pyproject.toml", "ai-hats")
    _pyproject(tmp_path / "packages" / "zeta" / "pyproject.toml", "ai-hats-zeta")
    _pyproject(tmp_path / "packages" / "alpha" / "pyproject.toml", "ai-hats-alpha")

    assert workspace_members(tmp_path) == ["ai-hats", "ai-hats-alpha", "ai-hats-zeta"]


def test_commands_prune_then_clean_by_name_only() -> None:
    """A bare ``uv cache clean`` would drop the warm third-party wheels — never."""
    assert reclaim_commands("/bin/uv", ["ai-hats", "ai-hats-core"]) == [
        ["/bin/uv", "cache", "prune"],
        ["/bin/uv", "cache", "clean", "ai-hats", "ai-hats-core"],
    ]


def test_reclaim_runs_both_bounded_and_reports_uv_summary(repo: Path) -> None:
    """Prune then clean, each with uv's lock wait bounded, uv's last line reported."""
    run = _Runner(
        _ok("Pruning cache at: x\nRemoved 81 files (240MiB)"), _ok("Removed 6 files (18MiB)")
    )

    report = reclaim(repo, run=run, which=lambda _: "/bin/uv", environ={"HOME": "/h"})

    assert [c["cmd"] for c in run.calls] == reclaim_commands("/bin/uv", ["ai-hats", "ai-hats-core"])
    for call in run.calls:
        assert call["env"] == {"HOME": "/h", "UV_LOCK_TIMEOUT": str(LOCK_WAIT_S)}
        assert call["timeout"] > LOCK_WAIT_S
    assert report == [
        "uv cache prune: Removed 81 files (240MiB)",
        "uv cache clean ai-hats ai-hats-core: Removed 6 files (18MiB)",
    ]


def test_reclaim_without_uv_runs_nothing(repo: Path) -> None:
    run = _Runner()

    report = reclaim(repo, run=run, which=lambda _: None, environ={})

    assert run.calls == []
    assert report == ["uv not on PATH — uv cache left as is"]


def test_a_stuck_prune_is_reported_and_the_clean_still_runs(repo: Path) -> None:
    """Warn-continue: a session's exit status is never the reclaim's to change."""
    stuck = subprocess.TimeoutExpired(["uv", "cache", "prune"], 120)
    busy = subprocess.CompletedProcess(
        [], 2, stdout="", stderr="error: Timeout (60s) when waiting for lock"
    )
    run = _Runner(stuck, busy)

    report = reclaim(repo, run=run, which=lambda _: "/bin/uv", environ={})

    assert len(run.calls) == 2
    assert report[0].startswith("uv cache prune: Command '['uv', 'cache', 'prune']' timed out")
    assert (
        report[1]
        == "uv cache clean ai-hats ai-hats-core: error: Timeout (60s) when waiting for lock"
    )


@pytest.mark.parametrize(
    ("exitstatus", "environ", "expected"),
    [
        (pytest.ExitCode.OK, {}, True),
        (pytest.ExitCode.TESTS_FAILED, {}, True),  # red keeps the sandbox, not the cache
        (pytest.ExitCode.OK, {"PYTEST_XDIST_WORKER": "gw3"}, False),  # the controller's turn
        (pytest.ExitCode.INTERRUPTED, {}, False),
        (pytest.ExitCode.USAGE_ERROR, {}, False),
        (pytest.ExitCode.NO_TESTS_COLLECTED, {}, False),
    ],
)
def test_reclaims_once_per_session_after_a_run_that_executed(exitstatus, environ, expected) -> None:  # noqa: ANN001
    assert reclaims_at_session_end(exitstatus, environ) is expected
