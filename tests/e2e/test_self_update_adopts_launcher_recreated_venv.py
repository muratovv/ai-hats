"""e2e (HATS-1998)

flow: a project on the versioned layout whose live versions/<sha> venv broke
      (its ai_hats package deleted — a host python upgrade or a torn-down
      checkout leaves the same shape) and is already at the edge repo's HEAD
cmds:
    ai-hats self update --force-downgrade
expect: the launcher recreates versions/<sha>; the python side verifies and
        adopts that venv (sentinel written, current kept) and exits 0
why: the launcher's recreate carries no .complete sentinel, so the managed
     update read the target as crash residue and rmtree'd the very venv it was
     running in, dying with a ModuleNotFoundError traceback"""
# comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.repo_src import build_src

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats_core.layout import ProjectLayout

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # real uv installs at call time → capped via conftest


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _bootstrap(tmp_path: Path) -> tuple[Path, Path, dict]:
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    user_home = tmp_path / "userhome"
    launcher.parent.mkdir(parents=True)
    project.mkdir()
    user_home.mkdir()
    pin_edge_channel(project)

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("AI_HATS_")
        and k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH")
    }
    env["AI_HATS_USER_HOME"] = str(user_home)
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env["AI_HATS_BUMP_BACKUP_DIR"] = str(tmp_path / "backups")
    env["COLUMNS"] = "400"

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher), "self", "update", "--force-downgrade"], cwd=project, env=env, timeout=300)
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project,
        env=env,
        timeout=60,
    )
    pin_edge_channel(project)
    return launcher, project, env


@pytest.mark.integration
def test_self_update_adopts_the_venv_the_launcher_recreated(tmp_path: Path) -> None:
    launcher, project, env = _bootstrap(tmp_path)
    versions = ProjectLayout.at(project).versions
    current = versions.current_pointer.read_text().strip()
    vdir = versions.dir(current)
    assert versions.sentinel(current).is_file(), "premise: the bootstrap left a complete version"

    # Break the live venv the way a host upgrade or a torn-down checkout does:
    # the interpreter stays, the package is gone.
    broken = next(vdir.glob("lib/python*/site-packages/ai_hats"))
    shutil.rmtree(broken)

    res = _run(
        [str(launcher), "self", "update", "--force-downgrade"],
        cwd=project,
        env=env,
        timeout=300,
    )
    combined = res.stdout + res.stderr
    assert "recreating" in combined, f"premise: the launcher did not heal:\n{combined}"
    assert "Traceback" not in combined and "ModuleNotFoundError" not in combined, combined
    assert "Adopted" in combined, combined
    assert versions.sentinel(current).is_file(), "the adopted venv carries its sentinel"
    assert versions.current_pointer.read_text().strip() == current
    assert (vdir / "bin" / "python").exists()

    # The adopted venv runs the tool.
    check = _run([str(launcher), "self", "update", "--check"], cwd=project, env=env, timeout=120)
    assert "BROKEN" not in check.stdout, check.stdout
