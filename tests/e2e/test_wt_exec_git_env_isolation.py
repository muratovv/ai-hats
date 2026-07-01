"""e2e (HATS-887): ``ai-hats wt exec -- git …`` strips an ambient GIT_* so the
inner command resolves the WORKTREE, not a leaked GIT_DIR.

Issued from the MAIN checkout on purpose: there ``_resolve_worktree`` uses the
filesystem ``list_active`` path (unpoisoned by GIT_DIR), so the only thing the
ambient GIT_DIR can still corrupt is the inner ``git`` that ``wt exec`` spawns —
exactly the fix under test. Fail-under-revert: drop the GIT_* pop in ``wt_exec``
and the inner ``rev-parse --absolute-git-dir`` returns the main ``.git``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _ai_hats(binary: Path, *args: str, cwd: Path, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=env if env is not None else {**os.environ},
        capture_output=True,
        text=True,
        timeout=120,
    )


def _tracker(root: Path) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"


def _find_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


def test_wt_exec_strips_ambient_git_env(tmp_project, tmp_path):
    main = tmp_project
    binary = main.ai_hats_binary

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    # A managed worktree is born on `transition execute`.
    assert _ai_hats(binary, "task", "create", "A", "--id", "HATS-1", cwd=main.path).returncode == 0
    assert _ai_hats(binary, "task", "transition", "HATS-1", "plan", cwd=main.path).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)
    r = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=main.path)
    assert r.returncode == 0, r.stderr
    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "a worktree must exist after execute"

    main_git_dir = (main.path / ".git").resolve()

    # Issue from MAIN with an ambient GIT_DIR pinned at the main repo. Resolution
    # (list_active) is filesystem-based, so it is unaffected; only the inner
    # `git` spawned by `wt exec` can still be poisoned — which the fix prevents.
    env = {**os.environ, "GIT_DIR": str(main_git_dir), "GIT_WORK_TREE": str(main.path.resolve())}
    res = _ai_hats(
        binary, "wt", "exec", "--", "git", "rev-parse", "--absolute-git-dir",
        cwd=main.path, env=env,
    )
    assert res.returncode == 0, f"wt exec failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"

    resolved = Path(res.stdout.strip().splitlines()[-1]).resolve()
    assert resolved != main_git_dir, (
        "🐛 HATS-887 REGRESSION: `wt exec` leaked the ambient GIT_DIR — the inner "
        f"git resolved the MAIN repo {main_git_dir}, not the worktree gitdir."
    )
    assert "worktrees" in str(resolved), (
        f"inner git should resolve the linked-worktree gitdir, got {resolved}"
    )
