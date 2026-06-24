"""E2E (HATS-823): a wt_in hook runs AFTER `git worktree add`, via the real binary.

The fixture `wt_in` hook records the worktree path it was handed. Proving the
path is the linked worktree (a temp `ai-hats-wt-*` dir), not the project root,
confirms wt_in runs post-checkout (ADR-0012 Revisions #1 — git refuses a
non-empty target dir, so the seed cannot run before `git worktree add`).

fail-under-revert: drop the `_run_wt_in_hooks()` call from `create()` and the
`.seeded` marker never appears → this test goes red.

Per dev_rule_e2e_gate: real bash + real pip + real ai-hats binary,
@pytest.mark.integration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "wt_hook_lib"


def _run(cmd, *, cwd, env, timeout=180, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def installed_launcher(shared_launcher, tmp_path_factory):
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wtin-home"))
    return launcher, env, shared_venv


@pytest.mark.integration
def test_wt_in_runs_after_worktree_add(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _git(project, "add", "libraries")
    _git(project, "commit", "-m", "lib")
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "e2e-wthook-role", "--no-wizard", "--task-prefix", "TST"],
        cwd=project, env=env,
    )

    _run([str(launcher), "wt", "create", "task/seedprobe"], cwd=project, env=env)

    seeded = project / ".seeded"
    assert seeded.exists(), "wt_in hook did not run"
    recorded = seeded.read_text().strip()
    # The hook was handed the linked worktree path (post-add), not project root.
    assert recorded != str(project)
    assert "ai-hats-wt-" in recorded
