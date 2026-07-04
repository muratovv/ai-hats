"""e2e (HATS-913): ``ai-hats wt exec`` threads the worktree's ``packages/*/src``
into PYTHONPATH, so a workspace package imports from the WORKTREE, not via the
main checkout's editable installs (the Franken-mix).

Fail-under-revert: drop the ``workspace_pythonpath`` call in ``wt_exec`` (back
to ``src``-only) and the inner ``import mypkg`` raises ModuleNotFoundError —
``mypkg`` lives only under ``packages/mypkg/src``.
"""
from __future__ import annotations

import os
import subprocess
import sys
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


def _child_env(repo_root: Path) -> dict[str, str]:
    """Subprocess env pointed at the checkout under test (worktree or main)."""
    from _helpers.env import checkout_pythonpath

    env = {**os.environ}
    env["PYTHONPATH"] = checkout_pythonpath(repo_root)
    env["AI_HATS_LIBRARY_ROOT"] = str(repo_root / "library")
    return env


def _ai_hats(binary: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
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


def _find_worktree(main: Path) -> Path | None:
    out = _git(main, "worktree", "list", "--porcelain").stdout
    for line in out.splitlines():
        if line.startswith("worktree ") and "ai-hats-wt" in line:
            return Path(line[len("worktree ") :])
    return None


def test_wt_exec_resolves_workspace_package_from_worktree(tmp_project, repo_root):
    main = tmp_project
    binary = main.ai_hats_binary
    env = _child_env(repo_root)

    pkg = main.path / "packages" / "mypkg" / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (main.path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(main.path, "init", "-b", "master")
    _git(main.path, "config", "user.email", "t@e")
    _git(main.path, "config", "user.name", "T")
    _git(main.path, "add", "-A")
    _git(main.path, "commit", "-m", "init")

    assert _ai_hats(binary, "task", "create", "A", "--id", "HATS-1", cwd=main.path, env=env).returncode == 0
    assert _ai_hats(binary, "task", "transition", "HATS-1", "plan", cwd=main.path, env=env).returncode == 0
    (_tracker(main.path) / "HATS-1" / "plan.md").write_text(_PLAN)
    r = _ai_hats(binary, "task", "transition", "HATS-1", "execute", cwd=main.path, env=env)
    assert r.returncode == 0, r.stderr
    wt = _find_worktree(main.path)
    assert wt is not None and wt.is_dir(), "a worktree must exist after execute"

    res = _ai_hats(
        binary, "wt", "exec", "--",
        sys.executable, "-c", "import mypkg; print(mypkg.__file__)",
        cwd=main.path, env=env,
    )
    assert res.returncode == 0, (
        "🐛 HATS-913 REGRESSION: `wt exec` left packages/*/src off PYTHONPATH — "
        f"the worktree's workspace package is invisible:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )

    resolved = Path(res.stdout.strip().splitlines()[-1]).resolve()
    assert resolved.is_relative_to(wt.resolve()), (
        f"mypkg must resolve from the worktree {wt}, got {resolved}"
    )
