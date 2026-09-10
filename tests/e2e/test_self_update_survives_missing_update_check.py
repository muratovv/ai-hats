"""e2e (HATS-987)

flow:   a developer running self update when update_check module is unavailable
cmds:
    ai-hats self update
expect: self update falls back safely and completes installation without update check
        telemetry
why: without update check fallback, missing telemetry modules break core self update
     functionality"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.project import pin_edge_channel
from _helpers.repo_src import build_src

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _all_managed_venvs(ah_dir: Path) -> list[Path]:
    """Every managed venv root under ``.agent/ai-hats/`` (default + versioned).

    A managed ``self update`` installs into ``versions/<sha>/`` (HATS-647), so the
    active venv the launcher resolves isn't necessarily ``.venv``. Collect both.
    """
    roots: list[Path] = []
    if (ah_dir / ".venv" / "bin" / "python").exists():
        roots.append(ah_dir / ".venv")
    versions = ah_dir / "versions"
    if versions.is_dir():
        roots += [d for d in versions.iterdir() if (d / "bin" / "python").exists()]
    return roots


def _installed_update_check_dirs(ah_dir: Path) -> list[Path]:
    """``site-packages/ai_hats/update_check`` inside every managed venv."""
    found: list[Path] = []
    for venv in _all_managed_venvs(ah_dir):
        for cand in sorted((venv / "lib").glob("python*/site-packages/ai_hats/update_check")):
            if cand.is_dir():
                found.append(cand)
    return found


def _bootstrap(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Install the launcher, bootstrap the managed venv, init a role."""
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    user_home = tmp_path / "userhome"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    user_home.mkdir()
    # HATS-764: edge so the bootstrap self update resolves the local source.
    pin_edge_channel(project)

    # Isolate from the developer's global config (mirrors test_self_update_resilient_config).
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("AI_HATS_")
        and k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH")
    }
    env["AI_HATS_USER_HOME"] = str(user_home)
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env["AI_HATS_BUMP_BACKUP_DIR"] = str(backups)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project,
        env=env,
        timeout=300,
    )  # HATS-675
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project,
        env=env,
        timeout=60,
    )
    pin_edge_channel(project)  # HATS-764: `self init` reset the harness block → re-pin edge
    return launcher_dest, project, env


@pytest.mark.integration
def test_self_update_survives_missing_update_check(tmp_path: Path) -> None:
    launcher_dest, project, env = _bootstrap(tmp_path)
    ah_dir = project / ".agent" / "ai-hats"

    # Simulate the packaging regression: drop update_check from every managed
    # venv's site-packages. The blue-green reinstall restores it into the NEW
    # version dir, but the guard + cache-drop run in this old, stripped venv.
    removed = _installed_update_check_dirs(ah_dir)
    assert removed, f"no installed ai_hats/update_check found under {ah_dir}"
    for d in removed:
        shutil.rmtree(d)

    # Fresh backup dir so the tarball we assert is from THIS update.
    backups = Path(env["AI_HATS_BUMP_BACKUP_DIR"])
    if backups.exists():
        shutil.rmtree(backups)

    res = _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project,
        env=env,
        timeout=300,  # HATS-675
    )

    # Primary: the degrade must exit 0 (fail-under-revert discriminator).
    assert res.returncode == 0, (
        f"self update must survive a missing update_check.\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    combined = res.stdout + res.stderr
    assert "ModuleNotFoundError" not in combined and "Traceback" not in combined, (
        f"self update leaked a traceback on the missing module.\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # Sanity: the update actually ran (bump-backup snapshot written pre-invalidate).
    tarballs = glob.glob(str(backups / "*.tar.gz"))
    assert tarballs, "no bump-backup tarball — the update did not run"
