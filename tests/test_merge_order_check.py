"""Behavioural tests for the merge-order check (HATS-2062): pre-merge gates the
card state (review road only), ->review gates a live review diff; unverifiable
fails closed (ADR-0019 D4). Driven as a script against a real rack backlog.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
CHECK = HOOKS_DIR / "merge_order_check.sh"

#: The rack console script lives beside the interpreter running this suite.
RACK = Path(sys.executable).parent / "rack"
pytestmark = pytest.mark.skipif(not RACK.is_file(), reason="rack console script not on this venv")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _env(repo: Path, point: str, **extra: str) -> dict[str, str]:
    """A minimal hook envelope: no ambient AI_HATS_* pins, rack on PATH, the
    cache home redirected into the sandbox (leaks trip HATS-1473's watcher)."""
    env = {
        "PATH": os.pathsep.join(
            [str(RACK.parent), "/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"]
        ),
        "AI_HATS_HOOK_POINT": point,
        "AI_HATS_PROJECT_DIR": str(repo),
        "AI_HATS_USER_HOME": str(repo.parent / "cache-home"),
    }
    env.update(extra)
    return env


def _rack(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RACK), *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60
    )


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CHECK)], env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repo on master with a rack backlog and one card (TEST-001)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "ai-hats.yaml").write_text("task_prefix: TEST\n", encoding="utf-8")
    _git(repo, "add", "ai-hats.yaml")
    _git(repo, "commit", "-q", "-m", "init")
    env = _env(repo, "test-setup")
    created = _rack(repo, env, "create", "probe card")
    assert created.returncode == 0, created.stderr
    return repo


_PLAN_FILL = (
    "\n## Requirements\n\nprobe\n\n## Scope & Out-of-scope\n\nprobe\n\n"
    "## Steps\n\nprobe\n\n## Verification Protocol\n\nprobe\n"
)


def _drive_state(repo: Path, target: str) -> None:
    """Walk the card to ``target`` along legal arrows (the engine's plan gate
    fires even on a forced jump, and plan->execute asks its ack)."""
    if target == "brainstorm":
        return  # a fresh card starts there
    walk = ["plan", "execute", "document", "review", "done"]
    env = _env(repo, "test-setup")
    for state in walk[: walk.index(target) + 1]:
        if state == "execute":
            plan_md = repo / ".agent/ai-hats/tracker/backlog/tasks/TEST-001/plan.md"
            plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_FILL, encoding="utf-8")
            env["AI_HATS_PLAN_ACK"] = "1"
        moved = _rack(repo, env, "transition", "TEST-001", state)
        assert moved.returncode == 0, moved.stdout + moved.stderr


# --- wt:pre-merge ----------------------------------------------------------


def test_pre_merge_passes_a_non_task_branch(project: Path) -> None:
    run = _run(_env(project, "pre-merge", AI_HATS_BRANCH_NAME="feature/foo"))
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("state", ["brainstorm", "plan", "execute", "document"])
def test_pre_merge_refuses_before_the_review_road(project: Path, state: str) -> None:
    _drive_state(project, state)
    run = _run(_env(project, "pre-merge", AI_HATS_BRANCH_NAME="task/test-001"))
    assert run.returncode == 2
    assert state in run.stdout


@pytest.mark.parametrize("state", ["review", "done"])
def test_pre_merge_passes_on_the_review_road(project: Path, state: str) -> None:
    _drive_state(project, state)
    run = _run(_env(project, "pre-merge", AI_HATS_BRANCH_NAME="task/test-001"))
    assert run.returncode == 0, run.stdout + run.stderr


def test_pre_merge_fails_closed_on_an_unknown_card(project: Path) -> None:
    run = _run(_env(project, "pre-merge", AI_HATS_BRANCH_NAME="task/test-999"))
    assert run.returncode == 2
    assert "fail-closed" in run.stdout or "cannot read" in run.stdout


def test_pre_merge_fails_closed_without_rack(project: Path) -> None:
    env = _env(project, "pre-merge", AI_HATS_BRANCH_NAME="task/test-001")
    env["PATH"] = "/usr/bin:/bin"  # no rack
    run = _run(env)
    assert run.returncode == 2


# --- rack ->review ---------------------------------------------------------


def _seed_task_branch(project: Path, *, merge: bool) -> None:
    _git(project, "checkout", "-q", "-b", "task/test-001")
    (project / "feature.txt").write_text("work\n", encoding="utf-8")
    _git(project, "add", "feature.txt")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "work")
    _git(project, "checkout", "-q", "master")
    if merge:
        _git(
            project,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "merge",
            "-q",
            "--no-ff",
            "task/test-001",
            "-m",
            "early merge",
        )


def test_review_edge_refuses_a_pre_merged_branch(project: Path) -> None:
    _seed_task_branch(project, merge=True)
    run = _run(_env(project, "document->review", AI_HATS_TASK_ID="TEST-001"))
    assert run.returncode == 2
    assert "already fully contained" in run.stdout


def test_review_edge_passes_a_live_branch(project: Path) -> None:
    _seed_task_branch(project, merge=False)
    run = _run(_env(project, "document->review", AI_HATS_TASK_ID="TEST-001"))
    assert run.returncode == 0, run.stdout + run.stderr


def test_review_edge_passes_a_branchless_card(project: Path) -> None:
    run = _run(_env(project, "document->review", AI_HATS_TASK_ID="TEST-001"))
    assert run.returncode == 0, run.stdout + run.stderr


def test_review_edge_fails_closed_without_a_base(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    run = _run(_env(repo, "document->review", AI_HATS_TASK_ID="TEST-001"))
    assert run.returncode == 2


def test_unknown_point_fails_closed(project: Path) -> None:
    run = _run(_env(project, "wt:create"))
    assert run.returncode == 2
