"""E2E: the legacy .venv is reclaimed once versioned is healthy (HATS-653 / Phase B).

Value under test: after lazy migration to the versioned layout, the orphaned
pre-versioning ``<ai_hats_dir>/.venv`` is reclaimed — but only once a process
actually runs from a complete versioned venv. Exercised with a real launcher +
real pip + real ``ai-hats self update`` (per ``dev_rule_e2e_gate``), no flaky
race: the two-step flow is deterministic.

Flow:
  1. Fresh project → first ``self update``. The launcher bootstraps the default
     ``.venv`` (migration), then the python self-update builds ``versions/<shaA>``
     and flips ``current``. This first updater runs **from** ``.venv``
     (``current_run_sha`` is None) → the reclaim guard skips, ``.venv`` is kept.
  2. Second ``self update`` (HEAD advanced → shaB). Now the launcher resolves
     ``current → versions/<shaA>`` and runs the updater **from** the versioned
     venv (``current_run_sha`` resolves) → the reclaim fires at self-update
     start, discarding ``.venv``.

Fail-under-revert:
  - reverting the reclaim → step-2 ``not .venv.exists()`` fails (stale fallback
    lingers);
  - reverting the ``current_run_sha`` guard (always reclaim) → step-1
    ``.venv.exists()`` fails (the first update deletes the venv it runs from).
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
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-653 e2e\n")
    _git(["add", marker], src_repo)
    _git(["commit", "--quiet", "-m", f"test: advance HEAD ({marker})"], src_repo)
    return _head_sha(src_repo)


@pytest.mark.integration
def test_e2e_legacy_venv_reclaimed_once_versioned_healthy(tmp_path: Path) -> None:
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    _git(["config", "user.email", "e2e@test"], src_repo)
    _git(["config", "user.name", "E2E"], src_repo)
    sha_a = _head_sha(src_repo)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop("AI_HATS_VENV", None)
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
