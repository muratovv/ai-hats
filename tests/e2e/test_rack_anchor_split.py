"""e2e (HATS-1573)

flow:   a developer running rack from inside a linked worktree against a backlog
        that belongs to a DIFFERENT project
cmds:
    rack create "sandbox card" --tasks-dir <sandbox>/.agent/ai-hats/tracker/backlog/tasks
    rack context <id> --tasks-dir <sandbox>/...
    rack context <main id>
expect: ids carry the sandbox project's prefix, the sandbox gets its own
        STATE.md, the enclosing checkout is not written to, and the same
        worktree WITHOUT an override still resolves the main tracker
why:    --tasks-dir moves the backlog without moving the operator; everything
        computed from the anchor used to read a foreign checkout, so one
        project's gates, prefix and STATE.md reached another project's backlog
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _helpers.git import git as _git
from _helpers.sessions import stand_in_session

pytestmark = [pytest.mark.integration, pytest.mark.rack]

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
STATE_MD = Path(".agent") / "ai-hats" / "STATE.md"


def _rack(rack: Path, *args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(rack), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=90
    )


def _init_project(root: Path) -> None:
    """Production shape: the tracker is gitignored, so a linked worktree carries
    neither ``.agent/`` nor a tracked ``ai-hats.yaml`` (the C2 case)."""
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")


@pytest.fixture
def split(shared_launcher, tmp_path):
    """A main checkout, one of its linked worktrees, and a foreign backlog."""
    _launcher, base_env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"

    main = tmp_path / "proj"
    main.mkdir()
    _init_project(main)
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", str(worktree), "-b", "task/probe")

    env = {
        **base_env,
        "AI_HATS_ROOT_PID": str(os.getpid()),
    }
    # HATS-1594: a session is its envelope; the bare id reads as an older build.
    # Named against `main`: a linked worktree's session still belongs to the
    # checkout that owns it, which is the split this file is about.
    stand_in_session(env, main, "e2e-anchor-split")
    return rack, main, worktree, env


def test_an_explicit_tasks_dir_anchors_the_backlog_at_its_own_project(split, tmp_path):
    """HATS-1573: composition, prefix and STATE.md follow the backlog's project."""
    rack, main, worktree, env = split
    sandbox = tmp_path / "sbx"
    (sandbox / TASKS_SUB).mkdir(parents=True)
    (sandbox / "ai-hats.yaml").write_text("task_prefix: OWN\n")

    seeded = _rack(rack, "create", "main card", cwd=main, env=env)
    assert seeded.returncode == 0, seeded.stderr
    main_state_before = (main / STATE_MD).read_bytes()

    made = _rack(
        rack,
        "create",
        "sandbox card",
        "--tasks-dir",
        str(sandbox / TASKS_SUB),
        cwd=worktree,
        env=env,
    )

    assert made.returncode == 0, made.stderr
    # The id names the backlog's project, not the checkout the operator stands in.
    assert "OWN-001" in made.stdout, made.stdout
    assert (sandbox / TASKS_SUB / "OWN-001" / "task.yaml").is_file()
    # And the enclosing checkout is left exactly as it was.
    assert (main / STATE_MD).read_bytes() == main_state_before
    assert not (main / TASKS_SUB / "OWN-001").exists()
    assert (sandbox / STATE_MD).is_file(), "the backlog indexes itself, in its own project"
    assert "OWN-001" in (sandbox / STATE_MD).read_text(encoding="utf-8")

    read_back = _rack(
        rack, "context", "OWN-001", "--tasks-dir", str(sandbox / TASKS_SUB), cwd=worktree, env=env
    )
    assert read_back.returncode == 0, read_back.stderr


def test_without_an_override_a_worktree_still_resolves_the_main_tracker(split):
    """HATS-1038 C2, unchanged: no override means the anchor still owns the backlog."""
    rack, main, worktree, env = split
    seeded = _rack(rack, "create", "main card", cwd=main, env=env)
    assert seeded.returncode == 0, seeded.stderr

    from_wt = _rack(rack, "context", "SBX-001", cwd=worktree, env=env)

    assert from_wt.returncode == 0, from_wt.stderr
    assert "SBX-001" in from_wt.stdout
    assert not (worktree / ".agent").exists(), "resolution must not mkdir a tracker here"


def test_an_anchorless_backlog_says_so_instead_of_dropping_checks_in_silence(split, tmp_path):
    """A scratch backlog composes no role — and says so, so a gate that is not
    there cannot be mistaken for a gate that passed."""
    rack, _main, worktree, env = split
    scratch = tmp_path / "scratch" / "tasks"
    scratch.mkdir(parents=True)

    made = _rack(rack, "create", "scratch card", "--tasks-dir", str(scratch), cwd=worktree, env=env)
    assert made.returncode == 0, made.stderr
    moved = _rack(
        rack, "transition", "HATS-001", "plan", "--tasks-dir", str(scratch), cwd=worktree, env=env
    )

    assert moved.returncode == 0, moved.stderr
    assert "no project owns" in moved.stderr, moved.stderr
    assert str(scratch) in moved.stderr
