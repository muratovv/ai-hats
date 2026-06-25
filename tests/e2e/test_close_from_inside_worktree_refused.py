"""e2e (HATS-788): a worktree-backed `task transition done` issued from INSIDE
the task's own linked worktree must be refused BEFORE teardown.

Otherwise `git worktree remove --force` deletes the operator's cwd and every
later `ai-hats` mis-resolves the tracker — a sibling task reads "not found"
even though it is intact on disk.

Fail-under-revert: without the guard the close merges, removes the cwd, and the
sibling lookup from the dead cwd fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _ai_hats(binary: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
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
    binary = main.ai_hats_binary

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # Task A (worktree-backed) walked to review + sibling B.
    assert _ai_hats(binary, "task", "create", "A", "--id", "HATS-1", cwd=main.path).returncode == 0
    assert _ai_hats(binary, "task", "create", "B", "--id", "HATS-2", cwd=main.path).returncode == 0
    assert _ai_hats(binary, "task", "transition", "HATS-1", "plan", cwd=main.path).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)
    r = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=main.path)
    assert r.returncode == 0, r.stderr
    assert _ai_hats(binary, "task", "transition", "HATS-1", "document", cwd=main.path).returncode == 0
    assert _ai_hats(
        binary, "task", "transition", "HATS-1", "review", "--final-state", "done", cwd=main.path
    ).returncode == 0

    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "worktree should exist after execute"

    # From INSIDE the worktree: refuse before any teardown.
    refused = _ai_hats(binary, "task", "transition", "HATS-1", "done", cwd=wt)
    combined = refused.stdout + refused.stderr
    assert refused.returncode != 0, combined
    assert "linked worktree" in combined, combined
    # Untouched: worktree still there, task still review, sibling resolvable.
    assert wt.is_dir(), "refused close must not remove the worktree"
    shown = _ai_hats(binary, "task", "show", "HATS-1", "--short", cwd=main.path)
    assert "review" in shown.stdout, shown.stdout
    sib = _ai_hats(binary, "task", "show", "HATS-2", "--short", cwd=main.path)
    assert sib.returncode == 0 and "HATS-2" in sib.stdout, sib.stdout + sib.stderr

    # From MAIN: the close succeeds and the sibling stays resolvable.
    done = _ai_hats(binary, "task", "transition", "HATS-1", "done", cwd=main.path)
    assert done.returncode == 0, done.stdout + done.stderr
    sib2 = _ai_hats(binary, "task", "show", "HATS-2", "--short", cwd=main.path)
    assert sib2.returncode == 0, sib2.stdout + sib2.stderr
