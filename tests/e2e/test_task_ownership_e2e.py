"""e2e (HATS-955, HATS-1263)

flow:   multiple agent processes executing tasks in parallel
cmds:
    rack transition HATS-1 execute
expect: an active task lock prevents another live agent process from claiming the task
        while stale locks from terminated processes are reclaimed
why:    task ownership locks ensure single-agent execution per task while recovering
        automatically from crashed processes
"""

from __future__ import annotations
from _helpers.git import git as _git

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parents[2]

_PLAN = (
    "# Plan\n## Requirements\nr\n## Scope & Out-of-scope\nin; out\n"
    "## Steps\n1. s\n## Verification Protocol\nv\n"
)


def _rack(*args: str, cwd: Path, session: str, root_pid: int) -> subprocess.CompletedProcess[str]:
    """Run the backlog CLI (HATS-1263). No ``rack`` console script on this tier;
    PYTHONPATH puts the checkout in reach of ``python -m``."""
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_session

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(Path(sys.executable).parent.parent)
    env["AI_HATS_ROOT_PID"] = str(root_pid)
    # HATS-1594: a session is its envelope; the bare id reads as an older build.
    stand_in_session(env, cwd, session)
    # plan->execute is consent-gated on rack; ownership is what these tests probe.
    env["AI_HATS_PLAN_ACK"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
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


def _execute_task(main: Path, tid: str, session: str, root_pid: int) -> None:
    """create → plan (fill) → execute, as one agent that ends up owning ``tid``."""
    assert (
        _rack("create", tid, "--id", tid, cwd=main, session=session, root_pid=root_pid).returncode
        == 0
    )
    assert (
        _rack("transition", tid, "plan", cwd=main, session=session, root_pid=root_pid).returncode
        == 0
    )
    (_tracker(main) / tid / "plan.md").write_text(_PLAN)
    res = _rack("transition", tid, "execute", cwd=main, session=session, root_pid=root_pid)
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
    _init_repo(main)

    # Agent A claims HATS-1, then "crashes": its anchor pid is already dead.
    _execute_task(main, "HATS-1", session="sess-a", root_pid=_dead_pid())
    assert _owners(main)["HATS-1"]["session_id"] == "sess-a"

    # Agent B re-enters execute (the reclaim self-loop) and takes over.
    res = _rack("transition", "HATS-1", "execute", cwd=main, session="sess-b", root_pid=live_pid)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _owners(main)["HATS-1"]["session_id"] == "sess-b"


def test_live_owner_is_not_stolen(tmp_project, live_pid):
    main = tmp_project.path
    _init_repo(main)

    # Agent A claims HATS-1, anchored on a live process.
    _execute_task(main, "HATS-1", session="sess-a", root_pid=live_pid)

    # Agent B cannot reclaim a live owner.
    res = _rack("transition", "HATS-1", "execute", cwd=main, session="sess-b", root_pid=_dead_pid())
    assert res.returncode != 0
    assert "live agent" in (res.stdout + res.stderr).lower()
    assert _owners(main)["HATS-1"]["session_id"] == "sess-a"  # unchanged


def test_single_slot_blocks_second_task(tmp_project, live_pid):
    main = tmp_project.path
    _init_repo(main)

    # Agent A is executing HATS-1.
    _execute_task(main, "HATS-1", session="sess-a", root_pid=live_pid)

    # It cannot advance a *second* task while still holding HATS-1.
    assert (
        _rack(
            "create", "HATS-2", "--id", "HATS-2", cwd=main, session="sess-a", root_pid=live_pid
        ).returncode
        == 0
    )
    res = _rack("transition", "HATS-2", "plan", cwd=main, session="sess-a", root_pid=live_pid)
    assert res.returncode != 0
    assert "holds" in (res.stdout + res.stderr).lower()
