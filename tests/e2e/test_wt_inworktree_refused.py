"""e2e (HATS-788)

flow:   a developer issuing worktree lifecycle commands from inside a linked worktree
cmds:
    # from inside a linked worktree
    ai-hats wt merge
expect: worktree lifecycle commands issued from inside a worktree are refused
why:    worktree lifecycle operations must be run from main repository to avoid tearing
        down cwd
"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.wt]


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
