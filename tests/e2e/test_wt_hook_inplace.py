"""e2e (HATS-1269)

flow:   a developer committing code with in-place hook script modifications
cmds:
    git commit -m "update"
expect: hook scripts execute in-place without copying redundant files
why:    hooks must execute from canonical paths without unnecessary file materialization"""

from __future__ import annotations
from _helpers.git import git as _git

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.guards, pytest.mark.wt]

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


def _wt_path(project: Path, branch: str) -> Path | None:
    out = _git(project, "worktree", "list", "--porcelain").stdout
    cur: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and cur is not None:
            if line.strip().endswith("/" + branch):
                return cur
    return None


def _seed_project(launcher: Path, env: dict, project: Path, role: str) -> None:
    project.mkdir(parents=True, exist_ok=True)
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
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            role,
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=project,
        env=env,
    )


def _commit_in_worktree(wt: Path) -> None:
    (wt / "work.txt").write_text("x\n")
    _git(wt, "add", "work.txt")
    _git(wt, "-c", "commit.gpgsign=false", "commit", "-m", "work")


@pytest.mark.integration
def test_hook_survives_its_skill_leaving_the_composition(installed_launcher, tmp_path):
    """A live worktree's wt_out hook still runs after the project re-inits to a
    role that composes no skill — the HATS-833 failure mode, deleted."""
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _seed_project(launcher, env, project, "e2e-wthook-role")

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/uncomposed")
    wt = _wt_path(project, "task/uncomposed")
    assert wt is not None
    _commit_in_worktree(wt)

    # The declaring skill leaves the composition while the worktree is live.
    ai("self", "init", "-p", "claude", "-r", "e2e-wthook-plain-role", "--no-wizard")

    ai("wt", "merge")

    assert (project / ".drained").exists(), "the wt_out hook did not run after uncomposing"
    assert "merge" in (project / ".drained").read_text()
    assert _wt_path(project, "task/uncomposed") is None  # torn down, merge not blocked


@pytest.mark.integration
def test_hook_reads_a_file_shipped_beside_it(installed_launcher, tmp_path):
    """``bundle: dir`` — the hook's neighbours are on disk because it runs in
    its own skill directory, not in a flattened managed dir."""
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _seed_project(launcher, env, project, "e2e-wthook-role")

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/neighbour")
    wt = _wt_path(project, "task/neighbour")
    assert wt is not None
    _commit_in_worktree(wt)

    ai("wt", "merge")

    neighbour = project / ".neighbour"
    assert neighbour.exists(), "the hook could not read the file shipped beside it"
    assert neighbour.read_text().strip() == "neighbour-is-on-disk"


@pytest.mark.integration
def test_init_materializes_no_wt_hooks_dir(installed_launcher, tmp_path):
    """AC-7: the retired surface is not created on a fresh install."""
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _seed_project(launcher, env, project, "e2e-wthook-role")

    assert not (project / ".agent" / "ai-hats" / "library" / "wt-hooks").exists()
