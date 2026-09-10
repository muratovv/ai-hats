"""e2e (HATS-653)

flow:   a developer running self update after migrating to versioned venv layout
cmds:
    ai-hats self update
expect: self update reclaims unneeded legacy .venv directory freeing disk space
why: without legacy venv reclamation, orphaned .venv directories consume unnecessary
     disk space"""

from __future__ import annotations
from _helpers.git import git

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-653 e2e\n")
    git(src_repo, "add", marker)
    git(src_repo, "commit", "--quiet", "-m", f"test: advance HEAD ({marker})")
    return _head_sha(src_repo)


@pytest.mark.integration
def test_e2e_legacy_venv_reclaimed_once_versioned_healthy(tmp_path: Path) -> None:
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )
    git(src_repo, "config", "user.email", "e2e@test")
    git(src_repo, "config", "user.name", "E2E")
    git(src_repo, "checkout", "-B", "e2e-main")  # HATS-764: align ls-remote HEAD
    sha_a = _head_sha(src_repo)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    versions = project / ".agent" / "ai-hats" / "versions"
    legacy_venv = project / ".agent" / "ai-hats" / ".venv"

    # --- Step 1: first (migration) update — runs FROM .venv, keeps it. ---
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    assert (versions / "current").read_text().strip() == sha_a
    assert (versions / sha_a / ".complete").is_file()
    assert legacy_venv.is_dir(), (
        "legacy .venv must survive the first update (the updater ran from it; "
        "the current_run_sha guard must skip the reclaim)"
    )

    # --- Step 2: second update — runs FROM versioned, reclaims .venv. ---
    sha_b = _advance(src_repo, "E2E_B_M1.txt")
    assert sha_b != sha_a
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    assert (versions / "current").read_text().strip() == sha_b
    assert (versions / sha_b / ".complete").is_file()
    assert not legacy_venv.exists(), (
        "legacy .venv must be reclaimed once the updater runs from a versioned venv"
    )


def _git(args, cwd):
    return git(cwd, *args)
