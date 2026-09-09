"""e2e (HATS-823)

flow:   a developer performing worktree operations when lifecycle hook scripts fail
cmds:
    ai-hats wt create task/failing-hook
expect: worktree creation or deletion is refused when lifecycle hooks return non-zero
why:    worktree lifecycle hooks must fail closed to prevent operating with broken
        setups"""

from __future__ import annotations
from _helpers.git import git as _git

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.wt

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


def _branch_exists(project: Path, branch: str) -> bool:
    return bool(
        subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=str(project),
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


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
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "e2e-wthook-role",
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=project,
        env=env,
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
    out = re.sub(r"\s+", " ", res.stdout + res.stderr)
    assert "hook" in out.lower()
    assert "`ai-hats wt discard task/probe --skip-hooks` and repeat the command" in out
    assert _wt_path(project, "task/probe") is not None  # preserved
    assert _branch_exists(project, "task/probe")
    assert not (project / ".drained").exists()

    # Retry once the hook can pass → teardown completes (idempotent).
    (project / ".drain-fail").unlink()
    ai("wt", "discard", "task/probe")
    assert _wt_path(project, "task/probe") is None
    assert "discard" in (project / ".drained").read_text()


@pytest.mark.integration
def test_refusing_wt_out_reports_the_scripts_own_words(installed_launcher, tmp_path):
    """HATS-1151: the operator gets the hook's instruction, not ``hook exited 2``.

    fail-under-revert: restore the synthetic reason in ``worktree_hooks.py`` and
    the instruction never reaches the CLI, so both text assertions go red.
    """
    launcher, env, _ = installed_launcher
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/probe-reason")
    (project / ".drain-refuse").touch()

    res = ai("wt", "discard", "task/probe-reason", expect_exit=1)

    out = res.stdout + res.stderr
    assert "3 unresolved review notes in .hunk/notes.json" in out, out
    assert "hunk-notes.sh consume" in out, out
    assert _wt_path(project, "task/probe-reason") is not None  # refusal blocked teardown


@pytest.mark.integration
def test_a_refusal_reaches_the_operator_without_escape_codes(installed_launcher, tmp_path):
    """HATS-1161 through the real binary, with both vectors at once: the
    launcher runs under the ``FORCE_COLOR=3`` an agent session carries, and the
    hook also colours its own refusal.

    fail-under-revert, two independent probes: drop the env scrub in
    ``_hook_env`` and the FORCE_COLOR assertion reddens; drop the strip in
    ``_decode_tail`` and the escape-byte assertion reddens.
    """
    launcher, env, _ = installed_launcher
    env = {**env, "FORCE_COLOR": "3", "CLICOLOR_FORCE": "1"}
    project = tmp_path / "proj"
    _init(launcher, env, project)

    def ai(*args, expect_exit=0):
        return _run([str(launcher), *args], cwd=project, env=env, expect_exit=expect_exit)

    ai("wt", "create", "task/probe-ansi")
    (project / ".drain-ansi").touch()

    res = ai("wt", "discard", "task/probe-ansi", expect_exit=1)

    raw = res.stdout + res.stderr
    # ai-hats' OWN Rich output honours the FORCE_COLOR we set on the launcher and
    # highlights words mid-string, so content is asserted on the normalised text.
    # That normalisation is this card's spun-off half (suite-wide); here it keeps
    # the assertion about the hook, not about our terminal preferences.
    clean = re.sub(r"\x1b\[[0-9;]*m", "", raw)

    assert "drain: refusing" in clean, raw
    assert "FORCE_COLOR=unset" in clean, raw  # the hook never saw the forcing var
    assert "NO_COLOR=1" in clean, raw
    # A leaked code does not reach the CLI as a working escape: Rich mangles the
    # bare ESC and renders the rest as text, so the operator reads literal
    # "[31mdrain". Probing for a byte sequence Rich never emits would be inert —
    # the residue in the NORMALISED text is what an operator actually sees.
    assert "31m" not in clean, repr(raw)
    assert _wt_path(project, "task/probe-ansi") is not None


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
