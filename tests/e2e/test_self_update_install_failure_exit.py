"""e2e (HATS-549, HATS-718)

flow:   a developer running self update when package installation command fails
cmds:
    ai-hats self update
expect: self update exits with non-zero code, displays error log, and preserves existing
        venv
why: without install failure handling, failed uv pip installs corrupt current working
     virtual environments"""

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
    """Run a subprocess; assert exit code matches ``expect_exit``."""
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


@pytest.mark.integration
def test_e2e_self_update_install_failure_exits_nonzero(tmp_path: Path) -> None:
    """A managed ``self update`` whose pip install fails to build exits 1 and
    leaves ``versions/current`` pinned to the previous (working) sha."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    # ----- fixture: local src-repo (the non-editable install source) -----
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
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    # ----- 1. install launcher + first self update (managed, non-editable) -----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    versions = project / ".agent" / "ai-hats" / "versions"
    current = versions / "current"
    assert current.read_text().strip() == sha_a, "first update did not pin current → shaA"

    # ----- 2. advance src-repo HEAD → shaB with a BROKEN pyproject.toml -----
    # git resolves shaB fine (names the new version dir), but the working tree
    # pip builds is no longer installable → the pip-install branch fires.
    (src_repo / "pyproject.toml").write_text(
        "this is not valid TOML @@@ [[[ HATS-718 broken build\n"
    )
    git(src_repo, "add", "pyproject.toml")
    git(src_repo, "commit", "--quiet", "-m", "test: break pyproject so pip install fails")
    sha_b = _head_sha(src_repo)
    assert sha_b != sha_a

    # ----- 3. second self update → install build fails → exit 1, current stays -----
    failed = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project,
        env=env,
        timeout=300,
        expect_exit=1,
    )
    combined = failed.stdout + failed.stderr
    assert "Update failed" in combined, f"missing failure text; combined output:\n{combined}"
    # AC: current is NOT flipped — the tool still resolves the old, working sha.
    assert current.read_text().strip() == sha_a, (
        f"current flipped to a half-installed version: {current.read_text().strip()!r}"
    )
    # HATS-790: the surviving version dir's completeness is bin/python (the
    # launcher's usability signal), not the removed bin/ai-hats console script.
    assert (versions / sha_a / "bin" / "python").is_file(), (
        "previous working version dir was damaged by the failed update"
    )


def _git(args, cwd):
    return git(cwd, *args)
