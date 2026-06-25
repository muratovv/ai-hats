"""e2e (HATS-788): `wt merge` / `wt discard` / `wt create` issued from INSIDE a
linked worktree must be refused.

The pre-existing guard called `_guard_not_inside_linked_worktree(_project_dir())`
— but `_project_dir()` has hopped to the MAIN checkout, so the guard inspected
MAIN and silently no-op'd. `wt merge` from inside its own worktree then removed
the operator's cwd. The fix checks the raw `Path.cwd()`.

Fail-under-revert: without the raw-cwd guard, `wt merge` from inside the worktree
prints "Merged" (returncode 0).
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


def _find_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


@pytest.mark.parametrize(
    "subcmd",
    [["wt", "merge"], ["wt", "discard"], ["wt", "create", "task/other"]],
)
def test_wt_lifecycle_from_inside_worktree_refused(tmp_project, tmp_path, subcmd):
    main = tmp_project
    binary = main.ai_hats_binary

    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init", "--allow-empty")

    created = _ai_hats(binary, "wt", "create", "task/foo", cwd=main.path)
    assert created.returncode == 0, created.stderr

    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "wt create should produce a worktree"

    res = _ai_hats(binary, *subcmd, cwd=wt)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, f"{subcmd} from inside worktree must be refused\n{combined}"
    assert "linked worktree" in combined, combined
    # The guard refuses before any teardown — the worktree is untouched.
    assert wt.is_dir(), "refused command must not remove the worktree"
