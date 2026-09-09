"""e2e (HATS-788)

flow:   a maintainer closes a worktree-backed task while cd'd INSIDE that
        task's own linked worktree
cmds:
    # in an initialised ai-hats project (ai-hats.yaml + a git repo)
    rack create A --id HATS-1
    rack create B --id HATS-2
    rack transition HATS-1 plan       # then execute -> document -> review
    cd <HATS-1's worktree>
    rack transition HATS-1 done       # must refuse
    cd <main checkout>
    rack transition HATS-1 done       # must succeed
expect: the refusal exits non-zero and names "linked worktree"; the worktree
        survives it; HATS-1 stays in state review; sibling HATS-2 still
        resolves via `rack context`, both after the refusal and after the
        close finally issued from main
why:    without the guard the close merges and `git worktree remove --force`
        deletes the operator's cwd — every later `rack` then mis-resolves the
        tracker and a sibling task reads "not found" though it is intact on
        disk
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.wt]


def _rack(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The backlog CLI (HATS-1263). This tier ships no ``rack`` console script,
    but the dev venv has the package, so ``python -m`` reaches it."""
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env={**os.environ},
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


def _find_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


def test_task_done_from_inside_worktree_refused(tmp_project, tmp_path):
    main = tmp_project

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # Task A (worktree-backed) walked to review + sibling B.
    assert _rack("create", "A", "--id", "HATS-1", cwd=main.path).returncode == 0
    assert _rack("create", "B", "--id", "HATS-2", cwd=main.path).returncode == 0
    assert _rack("transition", "HATS-1", "plan", cwd=main.path).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)
    r = _rack("transition", "HATS-1", "execute", cwd=main.path)
    assert r.returncode == 0, r.stderr
    assert _rack("transition", "HATS-1", "document", cwd=main.path).returncode == 0
    assert (
        _rack("transition", "HATS-1", "review", "--final-state", "done", cwd=main.path).returncode
        == 0
    )

    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "worktree should exist after execute"

    # From INSIDE the worktree: refuse before any teardown. rack's guard is
    # narrower than the legacy one — only the task's OWN worktree — and this
    # cwd is exactly that (`rack_wiring.py::_guard_not_inside`).
    refused = _rack("transition", "HATS-1", "done", cwd=wt)
    combined = refused.stdout + refused.stderr
    assert refused.returncode != 0, combined
    assert "linked worktree" in combined, combined
    # Untouched: worktree still there, task still review, sibling resolvable.
    assert wt.is_dir(), "refused close must not remove the worktree"
    shown = _rack("context", "HATS-1", cwd=main.path)
    assert "state: review" in shown.stdout, shown.stdout
    sib = _rack("context", "HATS-2", cwd=main.path)
    assert sib.returncode == 0 and "HATS-2" in sib.stdout, sib.stdout + sib.stderr

    # From MAIN: the close succeeds and the sibling stays resolvable.
    done = _rack("transition", "HATS-1", "done", cwd=main.path)
    assert done.returncode == 0, done.stdout + done.stderr
    sib2 = _rack("context", "HATS-2", cwd=main.path)
    assert sib2.returncode == 0, sib2.stdout + sib2.stderr
