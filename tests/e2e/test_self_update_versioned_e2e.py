"""e2e (HATS-647)

flow:   a developer executing self update under versioned venv layout
cmds:
    ai-hats self update
expect: self update builds new versioned venv in versions/<sha>/ and atomically updates
        current symlink
why: without versioned venv builds, updates overwrite active venvs mid-session causing
     tool crashes"""

from __future__ import annotations
from _helpers.git import git

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

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
def test_e2e_self_update_blue_green_versioned(tmp_path: Path) -> None:
    """A managed ``self update`` installs into versions/<sha> + flips current,
    and a second update to a new sha preserves the previous version dir."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    # HATS-764: pin the edge channel so `self update` resolves the local
    # src-repo HEAD (the upstream-master probe names a different repo). With no
    # harness block the channel defaults to stable → PyPI (unpublished → 404).
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: edge\n"
    )

    # ----- fixture: local src-repo (the non-editable install source) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )
    git(src_repo, "config", "user.email", "e2e@test")
    git(src_repo, "config", "user.name", "E2E")
    # Align the clone's symbolic HEAD with its checked-out working tree so
    # `git ls-remote <src> HEAD` (what the edge resolver reads to name the
    # version dir) matches the installed source — robust whether REPO_ROOT is a
    # master checkout or a linked worktree on a feature branch.
    git(src_repo, "checkout", "-B", "e2e-main")
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
    assert versions.is_dir(), "versions/ not created — versioned install missing"
    assert current.is_file(), "versions/current pointer missing"
    cur_a = current.read_text().strip()
    assert cur_a == sha_a, f"current={cur_a!r} != installed HEAD {sha_a!r}"
    vdir_a = versions / sha_a
    # HATS-790: versioned-venv completeness is bin/python (no bin/ai-hats script).
    assert (vdir_a / "bin" / "python").is_file(), "versions/<shaA>/ venv incomplete (bin/python)"
    assert not (vdir_a / "bin" / "ai-hats").exists(), "HATS-790: no bin/ai-hats in versioned venv"

    # Marker proving the old version dir is not rebuilt by the next update.
    marker = vdir_a / "AINTTOUCHED"
    marker.write_text("pinned\n")

    # ----- 2. advance src-repo HEAD → shaB (trivial, still installable) -----
    (src_repo / "E2E_VERSIONED_MARKER.txt").write_text("hats-647 e2e\n")
    git(src_repo, "add", "E2E_VERSIONED_MARKER.txt")
    git(src_repo, "commit", "--quiet", "-m", "test: advance HEAD for versioned e2e")
    sha_b = _head_sha(src_repo)
    assert sha_b != sha_a

    # ----- 3. second self update → versions/<shaB> + current flip -----
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    assert current.read_text().strip() == sha_b, "current did not flip to shaB"
    assert (versions / sha_b / "bin" / "python").is_file(), "versions/<shaB>/ missing (bin/python)"
    # AC1: the previously-pinned version dir survives the update untouched.
    assert vdir_a.is_dir(), "versions/<shaA>/ was destroyed by the update"
    assert marker.read_text() == "pinned\n", "versions/<shaA>/ was rebuilt in place"

    # ----- 4. the real launcher (no env) resolves the new current end-to-end -----
    clean = {k: v for k, v in env.items() if k != ENV_AI_HATS_VENV}
    _run([str(launcher_dest), "--help"], cwd=project, env=clean, timeout=60)


def _git(args, cwd):
    return git(cwd, *args)
