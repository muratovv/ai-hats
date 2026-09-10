"""e2e (HATS-1346)

flow:   a developer merging a worktree branch with a stale expected tip SHA
cmds:
    # when expected_tip SHA does not match current branch tip
    ai-hats wt merge task/stale-ref-e2e
expect: merge is refused with WorktreeStaleRefError preserving worktree and branch
why:    expected_tip validation prevents race conditions by verifying branch head before
        merge
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import ai_hats_wt as wt
from ai_hats_wt import WorktreeManager, NOOP_LIFECYCLE

pytestmark = pytest.mark.wt


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
@pytest.mark.smoke
def test_e2e_wt_stale_ref_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stale expected_tip SHA refuses merge fail-closed; current tip SHA succeeds.

    Scenario:
      1. Create git repo + initial commit on main.
      2. Create worktree task/stale-ref-e2e.
      3. Make commit A on task/stale-ref-e2e (sha_a).
      4. Make commit B on task/stale-ref-e2e (sha_b).
      5. Attempt merge with expected_tip=sha_a -> WorktreeStaleRefError raised naming sha_a & sha_b,
         worktree and branch preserved.
      6. Attempt merge with expected_tip=sha_b -> succeeds, worktree and branch cleaned up.
    """
    monkeypatch.setenv("AI_HATS_MERGE_ACK", "1")
    project = tmp_path / "project"
    project.mkdir()

    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test.com")
    _git(project, "config", "user.name", "E2E Test")
    (project / "README.md").write_text("# main repo\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")

    mgr = WorktreeManager(project, branch_name="task/stale-ref-e2e", lifecycle=NOOP_LIFECYCLE)
    wt_path = mgr.create()
    mgr.save_state()

    _git(wt_path, "config", "user.email", "e2e@test.com")
    _git(wt_path, "config", "user.name", "E2E Test")

    (wt_path / "file1.txt").write_text("commit A")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "commit A")
    sha_a = _git(wt_path, "rev-parse", "HEAD").stdout.strip()

    (wt_path / "file2.txt").write_text("commit B")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "commit B")
    sha_b = _git(wt_path, "rev-parse", "HEAD").stdout.strip()

    assert sha_a != sha_b

    # ---- Step 5: expected_tip=sha_a fails closed ----
    with pytest.raises(wt.WorktreeStaleRefError) as exc_info:
        mgr.merge(expected_tip=sha_a)

    err_msg = str(exc_info.value)
    assert sha_a in err_msg
    assert sha_b in err_msg
    assert wt_path.exists()
    assert _git(project, "branch", "--list", "task/stale-ref-e2e").stdout.strip() != ""

    # ---- Step 6: expected_tip=sha_b succeeds ----
    mgr.merge(expected_tip=sha_b)
    assert not wt_path.exists()
    assert _git(project, "branch", "--list", "task/stale-ref-e2e").stdout.strip() == ""
    # sha_b is integrated into main
    assert _git(project, "merge-base", "--is-ancestor", sha_b, "HEAD").returncode == 0
