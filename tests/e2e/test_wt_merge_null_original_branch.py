"""End-to-end coverage for ``ai-hats wt merge`` on a state file whose
``original_branch`` is ``null`` (HATS-714).

A worktree state JSON can lose ``original_branch`` — corrupt write,
hand-edit, or a pre-versioned/legacy state file. ``_load_by_key`` then
sets ``_original_branch = data.get("original_branch")`` → ``None``. Every
guard in ``WorktreeManager.merge`` is gated on ``_original_branch is not
None``, so ``None`` falls straight through to ``_fast_forward_merge`` →
``git rev-parse None`` → an opaque ``TypeError`` traceback (worktree.py)
instead of an actionable refusal — the failure shape HATS-479/482 worked
to eliminate.

Per ``dev_rule_e2e_gate``: change to ``src/ai_hats/cli/worktree.py``
requires a real-launcher + real-binary e2e test. CliRunner / pipeline
tests do NOT satisfy the gate.

**Fail-under-revert**: remove the ``WorktreeStateIncompleteError`` guard at
the top of ``WorktreeManager.merge`` → ``wt merge`` reverts to dumping a
``TypeError`` traceback. The ``"Traceback" not in stderr`` /
``"incomplete worktree state" in stdout`` assertions then fail. The test
exercises the new typed-refusal behaviour, not a pre-existing guard.

Modelled on ``tests/e2e/test_wt_merge_head_wandered.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.integration
def test_e2e_wt_merge_null_original_branch(shared_launcher, tmp_path):
    """HATS-714: ``wt merge`` on a state file with ``original_branch=null``
    refuses with a typed, actionable message — never a raw traceback.

    Scenario:
      1. Bootstrap session-shared venv + ``self init``.
      2. ``git init``, initial commit, ``ai-hats wt create
         task/null-base-probe`` from the default branch.
      3. Corrupt the worktree state JSON: ``original_branch`` -> ``null``
         (simulates a hand-edited / legacy / partially-written file).
      4. ``ai-hats wt merge task/null-base-probe`` MUST exit 1 with a
         "incomplete worktree state" refusal that names ``original_branch``
         — and stderr MUST carry no Python ``Traceback`` / ``TypeError``.
      5. The refusal is raised before any mutation: the worktree branch is
         preserved.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180, cwd=project):
        return _run(
            [str(launcher_dest), *args],
            cwd=cwd, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 1. bootstrap project ----
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")

    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- 2. create worktree on a task branch ----
    ai_hats("wt", "create", "task/null-base-probe")

    # ---- 3. corrupt the state file: original_branch -> null ----
    state_path = (
        project / ".agent" / "ai-hats" / "sessions" / "worktrees"
        / "task-null-base-probe.json"
    )
    assert state_path.is_file(), (
        f"worktree state file not found at {state_path}"
    )
    data = json.loads(state_path.read_text())
    assert data.get("original_branch"), (
        f"precondition: state should start with a real original_branch, "
        f"got {data.get('original_branch')!r}"
    )
    data["original_branch"] = None
    state_path.write_text(json.dumps(data, indent=2))

    # ---- 4. wt merge refuses cleanly, no traceback ----
    res = ai_hats(
        "wt", "merge", "task/null-base-probe",
        expect_exit=1, cwd=project,
    )
    combined = res.stdout + res.stderr

    # The actionable refusal — names the condition and the missing field.
    assert "incomplete worktree state" in combined.lower(), (
        f"typed refusal not surfaced:\n{combined}"
    )
    assert "original_branch" in combined, (
        f"refusal must name the missing `original_branch` field:\n{combined}"
    )
    # The whole point of HATS-714: no opaque crash leaks to the operator.
    assert "Traceback" not in res.stderr, (
        f"a Python traceback leaked instead of a typed refusal:\n"
        f"{res.stderr}"
    )
    assert "TypeError" not in combined, (
        f"the opaque TypeError must be gone:\n{combined}"
    )
    # The misleading "left intact for retry" line belongs to the generic
    # merge-failure path, which the top-of-merge guard never reaches.
    assert "left intact for retry" not in combined, (
        f"refusal must not masquerade as a failed merge:\n{combined}"
    )

    # ---- 5. refused before mutation: worktree branch preserved ----
    branches = _git(
        project, "branch", "--list", "task/null-base-probe"
    ).stdout
    assert "task/null-base-probe" in branches, (
        f"refusal must preserve the worktree branch (no teardown on a "
        f"pre-mutation refusal):\n{branches}"
    )
