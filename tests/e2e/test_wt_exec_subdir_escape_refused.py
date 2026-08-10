"""e2e (HATS-1203)

flow: a developer attempting to pass a path traversing outside worktree root to wt exec
cmds:
    ai-hats wt exec --task HATS-1203 --pwd ../../escape -- pwd
expect: wt exec refuses path traversal escaping worktree root with error exit code
why: without path traversal guards, malicious commands write files outside target
     worktree boundaries"""

from __future__ import annotations

import pytest

from _helpers.wt import ai_hats, child_env, two_worktrees

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("escape", ["../..", "../../..", "sub/../..", "/tmp"])
def test_wt_exec_cd_refuses_to_escape_the_worktree(tmp_project, repo_root, escape):
    main = tmp_project
    env = child_env(repo_root)
    branch, wt = two_worktrees(main.path, env)
    (wt / "sub").mkdir()

    res = ai_hats(
        main.ai_hats_binary,
        "wt",
        "exec",
        branch,
        "-C",
        escape,
        "--",
        "pwd",
        cwd=main.path,
        env=env,
    )

    assert res.returncode != 0, (
        f"🐛 HATS-1205: `-C {escape}` escaped the worktree instead of being "
        f"refused. stdout:\n{res.stdout}"
    )
    assert str(wt.resolve()) not in res.stdout, (
        f"the refused command must not have run at all, got:\n{res.stdout}"
    )
