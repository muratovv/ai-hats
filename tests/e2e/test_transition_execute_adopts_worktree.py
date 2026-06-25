"""e2e (HATS-840): `task transition execute` issued from INSIDE a linked worktree
adopts that worktree instead of spinning up a fresh one off main.

The HATS-060 adopt short-circuit checked the main-hopped `self.project_dir`
(`_project_dir()` hops a linked worktree to MAIN, HATS-524), so from inside a
worktree it inspected MAIN → False → never fired → a second `task/<id>` worktree
was created off main. The fix threads the operator's raw `Path.cwd()` from the
CLI down through `transition(caller_cwd=...)` into `_setup_worktree`.

Only the real `_project_dir()` hop exercises the bug, so this lives at the e2e
tier — the unit test `test_state.py::test_execute_inside_linked_worktree_does_not_nest`
injects `project_dir = wt_path` and cannot reproduce it.

Fail-under-revert: without the cwd thread, `transition execute` from inside the
worktree creates a second `task/hats-1` worktree (two linked worktrees) and the
adoption hint never prints.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Pin the spawned `ai-hats` binary to THIS checkout's `src` (HATS-685): the
# autouse `_scrub_redirect_env` strips PYTHONPATH so a raw env copy would resolve
# the editable install (the MAIN checkout) — which lacks the fix while developing
# in a worktree. `parents[2]` is the repo root, so this stays portable post-merge.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _ai_hats(binary: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AI_HATS_VENV"] = str(Path(sys.executable).parent.parent)
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=env,
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

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # A planned, worktree-eligible task (plan filled so the execute gate passes).
    assert _ai_hats(binary, "task", "create", "A", "--id", "HATS-1", cwd=main.path).returncode == 0
    assert _ai_hats(binary, "task", "transition", "HATS-1", "plan", cwd=main.path).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)

    # The operator stands in a pre-existing linked worktree (created off main).
    assert _ai_hats(binary, "wt", "create", "task/foo", cwd=main.path).returncode == 0
    wt = _find_linked_worktree(main.path)
    assert wt is not None and wt.is_dir()

    # Execute issued from INSIDE the worktree → adopt it, do NOT spin a fresh one.
    res = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=wt)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, combined

    branches = _worktree_branches(main.path)
    assert "task/hats-1" not in branches, (
        f"fresh worktree spun up off main instead of adopting: {branches}"
    )
    assert "task/foo" in branches, branches
    assert "adopted" in combined, combined

    # The task did advance to execute.
    shown = _ai_hats(binary, "task", "show", "HATS-1", "--short", cwd=main.path)
    assert "execute" in shown.stdout, shown.stdout
