"""e2e (HATS-1306)

flow: a developer running any ai-hats command from inside a linked git worktree or
      subfolder of
        an onboarded project
cmds:
    ai-hats wt status
expect: launcher resolves project root to main checkout and executes using main project
        managed
        venv
why: without worktree root resolution, running commands inside git worktrees fails to
     find
        managed project venvs"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = [pytest.mark.integration, pytest.mark.install, pytest.mark.wt]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_venv(venv_path: Path, *, ai_hats_echo: str = "ai-hats-stub") -> None:
    bindir = venv_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)

    python_stub = bindir / "python"
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "-c" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "-m" && "${2:-}" == "ai_hats" ]]; then\n'
        "    shift 2\n"
        f'    echo "{ai_hats_echo}: $*"\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _make_executable(python_stub)


def _init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(repo_path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial commit"], cwd=repo_path, check=True)


def test_launcher_worktree_execution_success(tmp_path: Path) -> None:
    """Case 1 & 2: Launcher executed in a worktree (or deep subfolder) resolves
    to main repo's venv without false foreign pin warnings."""
    main_repo = tmp_path / "main-repo"
    _init_git_repo(main_repo)

    (main_repo / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
    )
    venv = main_repo / ".agent" / "ai-hats" / ".venv"
    _fake_venv(venv, ai_hats_echo="main-repo-stub")

    wt_dir = tmp_path / "wt-repo"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "task/wt-branch", str(wt_dir)],
        cwd=main_repo,
        check=True,
    )

    deep_dir = wt_dir / "deep" / "subfolder"
    deep_dir.mkdir(parents=True)

    env = os.environ.copy()
    env[ENV_AI_HATS_VENV] = str(venv.resolve())
    env[AI_HATS_PROJECT_DIR_ENV] = str(main_repo.resolve())

    # Case 1: Run launcher from worktree root
    res1 = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=str(wt_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res1.returncode == 0, f"launcher failed in worktree root:\n{res1.stderr}"
    assert "main-repo-stub: status" in res1.stdout
    assert "foreign to" not in res1.stderr
    assert "ignoring the leaked session pin" not in res1.stderr

    # Case 2: Run launcher from deep subfolder in worktree
    res2 = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=str(deep_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res2.returncode == 0, f"launcher failed in deep subfolder:\n{res2.stderr}"
    assert "main-repo-stub: status" in res2.stdout
    assert "foreign to" not in res2.stderr


def test_launcher_worktree_foreign_pin_dropped(tmp_path: Path) -> None:
    """Case 3: Session pinned to Project A (foreign) when launcher runs inside a
    worktree of Project B drops A's pin and uses B's venv."""
    repo_a = tmp_path / "proj-a"
    repo_b = tmp_path / "proj-b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    (repo_b / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
    )
    venv_b = repo_b / ".agent" / "ai-hats" / ".venv"
    _fake_venv(venv_b, ai_hats_echo="proj-b-stub")

    venv_a = repo_a / ".agent" / "ai-hats" / ".venv"

    wt_b = tmp_path / "wt-b"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "task/b-branch", str(wt_b)],
        cwd=repo_b,
        check=True,
    )

    env = os.environ.copy()
    env[ENV_AI_HATS_VENV] = str(venv_a)
    env[AI_HATS_PROJECT_DIR_ENV] = str(repo_a)

    res = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=str(wt_b),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"launcher failed:\n{res.stderr}"
    assert "proj-b-stub: status" in res.stdout
    assert "ignoring the leaked session pin" in res.stderr
    assert "foreign to" in res.stderr


def test_launcher_non_ai_hats_worktree_no_hop(tmp_path: Path) -> None:
    """Case 4: Non-ai-hats git worktree (no .agent/ or ai-hats.yaml in main checkout)
    does NOT hop, falls back to raw cwd and reports venv missing."""
    plain_repo = tmp_path / "plain-repo"
    _init_git_repo(plain_repo)

    wt_plain = tmp_path / "wt-plain"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "task/plain", str(wt_plain)],
        cwd=plain_repo,
        check=True,
    )

    env = os.environ.copy()
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop(AI_HATS_PROJECT_DIR_ENV, None)

    res = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=str(wt_plain),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "venv missing at" in res.stderr
    assert str(wt_plain) in res.stderr
