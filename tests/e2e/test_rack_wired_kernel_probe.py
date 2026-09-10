"""e2e (HATS-1263)

flow:   a developer running task lifecycle commands via the module invocation interface
cmds:
    rack create "wired probe" --role assistant
    rack transition SBX-001 execute
expect: STATE.md is refreshed on card creation and a git worktree is provisioned when
        transitioning to execute
why:    module-level rack execution must bind the full kernel extensions rather than
        falling back to a bare un-wired state
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
STATE_MD = Path(".agent") / "ai-hats" / "STATE.md"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)


def _rack(project: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    (root / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    (root / TASKS_SUB).mkdir(parents=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")
    return root


@pytest.fixture
def env(project: Path) -> dict[str, str]:
    """Takes ``project`` because a session envelope names the tree it composed
    against, and a bare id is not a session any launch produces (HATS-1594)."""
    from _helpers.env import checkout_pythonpath
    from _helpers.sessions import stand_in_session

    e = os.environ.copy()
    e["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, e.get("PYTHONPATH", ""))
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    return stand_in_session(e, project, "e2e-rack-wired-probe")


def test_shim_tier_create_refreshes_state_md(project: Path, env: dict[str, str]):
    """STATE.md refresh is a wired-kernel effect — the bare kernel has none."""
    created = _rack(project, "create", "wired probe", "--role", "assistant", env=env)
    assert created.returncode == 0, created.stderr
    assert "SBX-001" in created.stdout, created.stdout

    state_md = project / STATE_MD
    assert state_md.is_file(), (
        "wired `create` must refresh STATE.md; its absence means `python -m "
        "ai_hats_rack` resolved the BARE kernel (entry point unreachable)"
    )
    assert "SBX-001" in state_md.read_text(encoding="utf-8")


def test_shim_tier_execute_creates_a_real_worktree(project: Path, env: dict[str, str]):
    """The load-bearing one: every re-pointed worktree test depends on this."""
    assert _rack(project, "create", "wired probe", "--role", "assistant", env=env).returncode == 0
    assert _rack(project, "transition", "SBX-001", "plan", env=env).returncode == 0

    plan_md = project / TASKS_SUB / "SBX-001" / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")

    executed = _rack(
        project, "transition", "SBX-001", "execute", env={**env, "AI_HATS_PLAN_ACK": "1"}
    )
    assert executed.returncode == 0, executed.stderr

    wt_lines = [ln for ln in executed.stdout.splitlines() if ln.strip().startswith("Worktree:")]
    assert wt_lines, (
        "wired `execute` must create and print a worktree; no Worktree line means "
        f"the BARE kernel ran — worktree e2e coverage would be vacuous.\n{executed.stdout}"
    )
    worktree = Path(wt_lines[0].split("Worktree:", 1)[1].strip())
    assert (worktree / ".git").is_file(), "must be a real linked worktree, not a plain dir"


# NB: no plan-gate case here. `_bare_kernel` (cli_kernel.py:51-53) mounts the
# scaffold + plan-gate too, so a gate assertion passes on BOTH kernels and
# discriminates nothing. The worktree and STATE.md cases above are the two
# effects the bare kernel genuinely lacks.
