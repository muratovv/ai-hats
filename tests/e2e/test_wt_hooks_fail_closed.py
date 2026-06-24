"""E2E (HATS-823): wt_out hooks fire fail-closed at teardown, via the real binary.

A fixture skill (`e2e-wthook`) declares a `wt_out` drain hook bound to all
teardown routes. After a real `ai-hats self init` composes the role and
`wt create` seeds + persists the carry, a failing drain ABORTS `wt discard`
(worktree + branch preserved); `--skip-hooks` forces it through; a passing drain
runs on `wt merge` and then the worktree tears down.

fail-under-revert: drop the `_run_wt_out_hooks` call from `discard()` and the
discard tears down despite the failing hook → `test_failing_wt_out_aborts_discard`
goes red.

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


def _branch_exists(project: Path, branch: str) -> bool:
    return bool(
        subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=str(project), capture_output=True, text=True,
        ).stdout.strip()
    )


def _wt_path(project: Path, branch: str) -> Path | None:
    out = _git(project, "worktree", "list", "--porcelain").stdout
    cur: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Path(line[len("worktree "):].strip())
        elif line.startswith("branch ") and cur is not None:
            if line.strip().endswith("/" + branch):
                return cur
    return None


@pytest.fixture
def installed_launcher(shared_launcher, tmp_path_factory):
    """Read-only test on the session venv with a clean env (HATS-685/582):
    pop PYTHONPATH (else the launcher imports the source tree without
    ``library/``) and isolate HOME (no dev ``~/.ai-hats/`` bleed)."""
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wthook-home"))
    return launcher, env, shared_venv


def _init(launcher: Path, env: dict, project: Path) -> None:
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
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "e2e-wthook-role", "--no-wizard", "--task-prefix", "TST"],
        cwd=project, env=env,
    )


@pytest.mark.integration
def test_failing_wt_out_aborts_discard(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/probe")
    assert (project / ".seeded").exists()  # wt_in ran after checkout

    (project / ".drain-fail").touch()
    res = ai("wt", "discard", "task/probe", expect_exit=1)
    assert "hook" in (res.stdout + res.stderr).lower()
    assert _wt_path(project, "task/probe") is not None  # preserved
    assert _branch_exists(project, "task/probe")
    assert not (project / ".drained").exists()

    # Retry once the hook can pass → teardown completes (idempotent).
    (project / ".drain-fail").unlink()
    ai("wt", "discard", "task/probe")
    assert _wt_path(project, "task/probe") is None
    assert "discard" in (project / ".drained").read_text()


@pytest.mark.integration
def test_skip_hooks_forces_discard(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/probe2")
    (project / ".drain-fail").touch()
    ai("wt", "discard", "task/probe2", "--skip-hooks")  # forced through
    assert _wt_path(project, "task/probe2") is None
    assert not (project / ".drained").exists()  # hook was skipped


@pytest.mark.integration
def test_passing_wt_out_runs_on_merge(installed_launcher, tmp_path):
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/probe3")
    wtp = _wt_path(project, "task/probe3")
    assert wtp is not None
    (wtp / "work.txt").write_text("x")
    _git(wtp, "add", "work.txt")
    _git(wtp, "commit", "-m", "work")

    ai("wt", "merge", "task/probe3")
    assert "merge" in (project / ".drained").read_text()  # hook ran before teardown
    assert _wt_path(project, "task/probe3") is None  # torn down
