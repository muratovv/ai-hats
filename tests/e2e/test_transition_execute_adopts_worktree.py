"""e2e (HATS-840)

flow:   a developer transitioning a task to execute from inside a linked worktree
cmds:
    # from inside a linked worktree directory
    rack transition HATS-1 execute
expect: the existing worktree is adopted instead of provisioning a second worktree off
        main
why:    transitioning to execute from inside a worktree must adopt the caller worktree
        to prevent duplicate worktree creation
"""

from __future__ import annotations
from _helpers.git import init_repo, git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = [pytest.mark.integration, pytest.mark.wt]

# Pin the spawned `ai-hats` binary to THIS checkout's `src` (HATS-685): the
# autouse `_scrub_redirect_env` strips PYTHONPATH so a raw env copy would resolve
# the editable install (the MAIN checkout) — which lacks the fix while developing
# in a worktree. `parents[2]` is the repo root, so this stays portable post-merge.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _child_env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(Path(sys.executable).parent.parent)
    env["AI_HATS_PLAN_ACK"] = "1"  # plan->execute is consent-gated on rack
    return env


def _ai_hats(binary: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _rack(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _tracker(root: Path) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"


_PLAN = """# Plan
## Requirements
do the thing
## Scope & Out-of-scope
in: thing; out: other
## Steps
1. thing
## Verification Protocol
run it
"""


def _worktree_branches(main: Path) -> set[str]:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    branches: set[str] = set()
    for line in out.splitlines():
        if line.startswith("branch "):
            branches.add(line[len("branch ") :].removeprefix("refs/heads/"))
    return branches


def _find_linked_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


def test_transition_execute_from_inside_worktree_adopts(tmp_project, tmp_path):
    main = tmp_project
    binary = main.ai_hats_binary

    init_repo(main.path, branch="master")

    # A planned, worktree-eligible task (plan filled so the execute gate passes).
    assert _rack("create", "A", "--id", "HATS-1", cwd=main.path).returncode == 0
    assert _rack("transition", "HATS-1", "plan", cwd=main.path).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)

    # The operator stands in a pre-existing linked worktree (created off main).
    assert _ai_hats(binary, "wt", "create", "task/foo", cwd=main.path).returncode == 0
    wt = _find_linked_worktree(main.path)
    assert wt is not None and wt.is_dir()

    # Execute issued from INSIDE the worktree → adopt it, do NOT spin a fresh one.
    res = _rack("transition", "HATS-1", "execute", cwd=wt)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, combined

    branches = _worktree_branches(main.path)
    assert "task/hats-1" not in branches, (
        f"fresh worktree spun up off main instead of adopting: {branches}"
    )
    assert "task/foo" in branches, branches

    # rack prints no "adopted" token (legacy did) — assert the stronger fact:
    # the worktree it reports IS the pre-existing one.
    reported = [ln for ln in combined.splitlines() if ln.strip().startswith("Worktree:")]
    assert reported, f"execute must report a worktree:\n{combined}"
    adopted = Path(reported[0].split("Worktree:", 1)[1].strip()).resolve()
    assert adopted == wt.resolve(), f"must adopt {wt}, reported {adopted}"

    # The task did advance to execute.
    shown = _rack("context", "HATS-1", cwd=main.path)
    assert "state: execute" in shown.stdout, shown.stdout
