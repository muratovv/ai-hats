"""End-to-end coverage for the `ai-hats task attach …` CLI surface (HATS-402).

The unit suite covers reconcile / verify_manifest / Attachment validation,
but stubs the click wiring and the assembler→hook install chain. Per
`dev_rule_e2e_gate`, the new CLI commands plus the skill-declared
pre-commit hook need a real-subprocess test that would fail if the click
wiring drifted (subcommand moved, flag renamed) or if the hook stopped
shipping with the skill.

Slow (~60s on a warm pip cache). Marked `integration`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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


@pytest.mark.integration
def test_e2e_task_attach_full_lifecycle(tmp_path):
    """HATS-402 attachments CLI + pre-commit hook, real subprocess.

    1. Bootstrap: launcher + self update + self init (git-init'd project).
    2. Create TST-001, attach a file (move + manifest entry).
    3. Idempotency: second `attach add` of identical content → noop, no
       work_log diff.
    4. Collision: `attach add` of different content under the same name
       → exit 1 with the 'remove first' hint.
    5. `attach list` shows the entry; `attach show` prints content;
       `attach verify` returns exit 0 silent.
    6. Hook installation: after `self init` the pre-commit hook ships at
       `.githooks/pre-commit.d/backlog-manager-pre-commit-attachments.sh`
       and is executable.
    7. Divergence detection: drop an orphan blob under attachments/ and
       `attach verify` reports `+ orphan.md` with exit 1; registering it
       via `attach add` clears the divergence.
    8. Remove untracked → exit 2 without --yes; exit 0 with --yes.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    # git init so the assembler wires .githooks and ai-hats has a real
    # repo to interrogate (is_git_tracked needs one).
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(
        ["bash", str(INSTALL_LAUNCHER)],
        cwd=tmp_path, env=env, timeout=30,
    )
    assert launcher_dest.is_file()

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project ----
    ai_hats("self", "update")
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- 6. hook shipped + executable ----
    hook_path = (
        project / ".githooks" / "pre-commit.d"
        / "backlog-manager-pre-commit-attachments.sh"
    )
    assert hook_path.is_file(), f"hook missing at {hook_path}"
    assert os.access(hook_path, os.X_OK), f"hook not executable at {hook_path}"

    # ---- 2. create task + attach a file ----
    ai_hats("task", "create", "First", "-p", "medium")
    blob_src = tmp_path / "incoming.md"
    blob_src.write_text("hello attachments world\n")

    res = ai_hats("task", "attach", "add", "TST-001", str(blob_src))
    assert "Attached" in res.stdout, res.stdout
    # blob_src has been moved into the task's attachments/.
    assert not blob_src.exists(), "source should have been moved"
    landed = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog"
        / "tasks" / "TST-001" / "attachments" / "incoming.md"
    )
    assert landed.is_file(), f"blob not at {landed}"

    # ---- 5. list + show + verify ----
    res = ai_hats("task", "attach", "list", "TST-001")
    assert "incoming.md" in res.stdout, res.stdout

    res = ai_hats("task", "attach", "show", "TST-001", "incoming.md")
    assert "hello attachments world" in res.stdout, res.stdout

    ai_hats("task", "attach", "verify", "TST-001")  # exit 0 silent

    # ---- 3. idempotency ----
    # Re-add the exact same content under the same name → noop.
    same_src = tmp_path / "same.md"
    same_src.write_text("hello attachments world\n")
    res = ai_hats(
        "task", "attach", "add", "TST-001", str(same_src),
        "--name", "incoming.md",
    )
    assert "Noop" in res.stdout or "already attached" in res.stdout, res.stdout
    # The source should NOT have been moved on noop (idempotency
    # respects an untouched filesystem).
    assert same_src.exists(), "noop must not move the source file"

    # ---- 4. collision: different content, same name → exit 1 ----
    diff_src = tmp_path / "diff.md"
    diff_src.write_text("DIFFERENT content here\n")
    res = ai_hats(
        "task", "attach", "add", "TST-001", str(diff_src),
        "--name", "incoming.md",
        expect_exit=1,
    )
    assert "Collision" in res.stdout or "different content" in res.stdout, res.stdout
    # Source file untouched on collision.
    assert diff_src.exists(), "collision must not move the source file"

    # ---- 7. divergence detection ----
    orphan = landed.parent / "orphan.md"
    orphan.write_text("planted outside CLI\n")
    res = ai_hats("task", "attach", "verify", "TST-001", expect_exit=1)
    assert "+ orphan.md" in res.stdout, res.stdout
    # Registering it via the CLI clears the divergence.
    ai_hats("task", "attach", "add", "TST-001", str(orphan))
    ai_hats("task", "attach", "verify", "TST-001")  # exit 0 silent again

    # ---- 8. remove gate: untracked needs --yes ----
    # incoming.md is untracked (we never `git add` it in this test).
    res = ai_hats(
        "task", "attach", "remove", "TST-001", "incoming.md",
        expect_exit=2,
    )
    assert "Refusing" in res.stdout, res.stdout
    # With --yes the remove goes through.
    res = ai_hats("task", "attach", "remove", "TST-001", "incoming.md", "--yes")
    assert "Removed" in res.stdout, res.stdout
    # Verify is clean again (orphan.md is the only remaining entry).
    ai_hats("task", "attach", "verify", "TST-001")
