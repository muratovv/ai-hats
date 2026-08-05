"""End-to-end coverage for rebased branch refusal and containment (HATS-1370).

Per ``dev_rule_e2e_gate``: changes touching ``packages/ai-hats-wt/src/ai_hats_wt/manager.py``
or ``src/ai_hats/cli/worktree.py`` require an e2e test using the real launcher + real pip.
Verifies that:
1. Merging a rebased branch without --accept-drift exits 1 with WorktreeRebasedBranchError.
2. Merging a rebased branch with --accept-drift exits 0 and cleans up worktree + branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run(cmd, *, cwd, env, timeout=120, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


from _helpers.git import git as _git


def _locate_worktree(project: Path, branch: str) -> Path:
    listing = _git(project, "worktree", "list", "--porcelain").stdout
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch ") :].strip().endswith(f"/{branch}"):
                return current_path
    raise AssertionError(f"could not locate worktree for {branch}:\n{listing}")


@pytest.mark.integration
def test_e2e_wt_rebased_branch_refusal(shared_launcher, tmp_path):
    """Rebased branch refuses merge fail-closed; --accept-drift proceeds to clean up."""
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    merge_env = dict(env)
    merge_env["AI_HATS_MERGE_ACK"] = "1"

    def ai_hats(*args, expect_exit=0, timeout=120, cwd=project, custom_env=None):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd,
            env=custom_env or merge_env,
            timeout=timeout,
            expect_exit=expect_exit,
        )

    from _helpers.git import init_repo
    init_repo(project, branch="master")

    # Create worktree
    ai_hats("wt", "create", "task/rebased-e2e")
    wt_path = _locate_worktree(project, "task/rebased-e2e")

    # Make commit on worktree
    (wt_path / "rebased_file.txt").write_text("rebased content")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "worktree commit")
    wt_sha = _git(wt_path, "rev-parse", "HEAD").stdout.strip()

    # Advance master in main repo (simulates another commit on master)
    (project / "other.txt").write_text("other master work")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "other master commit")

    # Cherry-pick the worktree commit onto master (creates new SHA on master)
    _git(project, "cherry-pick", wt_sha)

    # 1. Attempt merge without --accept-drift -> refused with exit 1
    res = ai_hats("wt", "merge", "task/rebased-e2e", expect_exit=1)
    assert "Refused (rebased branch)" in res.stdout or "Refused (rebased branch)" in res.stderr
    assert "--accept-drift" in res.stdout or "--accept-drift" in res.stderr

    # 2. Attempt merge with --accept-drift -> succeeds with exit 0
    ai_hats("wt", "merge", "task/rebased-e2e", "--accept-drift", expect_exit=0)

    assert not wt_path.exists()
    assert _git(project, "branch", "--list", "task/rebased-e2e").stdout.strip() == ""
