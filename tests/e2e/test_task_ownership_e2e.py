"""e2e (HATS-955): task ownership across real ``ai-hats task`` processes.

Two separate ``ai-hats task`` invocations with distinct ``AI_HATS_ROOT_PID``
model two agents. Exercises the real cross-process path a unit test cannot: the
env-stamped liveness anchor + ``ps``-based reclaim-on-death + the fcntl-locked
registry file written by short-lived CLI subprocesses.

Fail-under-revert: without the ownership wiring a live owner is not protected
(the second agent steals the task) and a dead owner is never detected.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

_PLAN = (
    "# Plan\n## Requirements\nr\n## Scope & Out-of-scope\nin; out\n"
    "## Steps\n1. s\n## Verification Protocol\nv\n"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _ai_hats(
    binary: Path, *args: str, cwd: Path, session: str, root_pid: int
) -> subprocess.CompletedProcess[str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(Path(sys.executable).parent.parent)
    env["AI_HATS_SESSION_ID"] = session
    env["AI_HATS_ROOT_PID"] = str(root_pid)
    return subprocess.run(
        [str(binary), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120
    )


def _owners(root: Path) -> dict:
    p = root / ".agent" / "ai-hats" / "tracker" / "backlog" / "ownership.json"
    return json.loads(p.read_text())["owners"] if p.exists() else {}


def _tracker(root: Path) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"


def _init_repo(main: Path) -> None:
    (main / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@e")
    _git(main, "config", "user.name", "T")
    _git(main, "add", "-A")
    _git(main, "commit", "-m", "init", "--allow-empty")


def _execute_task(binary: Path, main: Path, tid: str, session: str, root_pid: int) -> None:
    """create → plan (fill) → execute, as one agent that ends up owning ``tid``."""
    assert _ai_hats(binary, "task", "create", tid, "--id", tid, cwd=main, session=session, root_pid=root_pid).returncode == 0
    assert _ai_hats(binary, "task", "transition", tid, "plan", cwd=main, session=session, root_pid=root_pid).returncode == 0
    (_tracker(main) / tid / "plan.md").write_text(_PLAN)
    res = _ai_hats(binary, "task", "transition", tid, "execute", cwd=main, session=session, root_pid=root_pid)
    assert res.returncode == 0, res.stdout + res.stderr


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def live_pid():
    """A pid that stays alive for the test (a real separate process)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc.pid
    finally:
        proc.terminate()
        proc.wait()


def test_reclaim_on_dead_owner(tmp_project, live_pid):
    main = tmp_project.path
    binary = tmp_project.ai_hats_binary
    _init_repo(main)

    # Agent A claims HATS-1, then "crashes": its anchor pid is already dead.
    _execute_task(binary, main, "HATS-1", session="sess-a", root_pid=_dead_pid())
    assert _owners(main)["HATS-1"]["session_id"] == "sess-a"

    # Agent B re-enters execute (the reclaim self-loop) and takes over.
    res = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=main, session="sess-b", root_pid=live_pid)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _owners(main)["HATS-1"]["session_id"] == "sess-b"


def test_live_owner_is_not_stolen(tmp_project, live_pid):
    main = tmp_project.path
    binary = tmp_project.ai_hats_binary
    _init_repo(main)

    # Agent A claims HATS-1, anchored on a live process.
    _execute_task(binary, main, "HATS-1", session="sess-a", root_pid=live_pid)

    # Agent B cannot reclaim a live owner.
    res = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=main, session="sess-b", root_pid=_dead_pid())
    assert res.returncode != 0
    assert "live agent" in (res.stdout + res.stderr).lower()
    assert _owners(main)["HATS-1"]["session_id"] == "sess-a"  # unchanged


def test_single_slot_blocks_second_task(tmp_project, live_pid):
    main = tmp_project.path
    binary = tmp_project.ai_hats_binary
    _init_repo(main)

    # Agent A is executing HATS-1.
    _execute_task(binary, main, "HATS-1", session="sess-a", root_pid=live_pid)

    # It cannot advance a *second* task while still holding HATS-1.
    assert _ai_hats(binary, "task", "create", "HATS-2", "--id", "HATS-2", cwd=main, session="sess-a", root_pid=live_pid).returncode == 0
    res = _ai_hats(binary, "task", "transition", "HATS-2", "plan", cwd=main, session="sess-a", root_pid=live_pid)
    assert res.returncode != 0
    assert "holds" in (res.stdout + res.stderr).lower()
