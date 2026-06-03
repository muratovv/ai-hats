"""E2E: ``ai-hats self update`` is blue-green versioned (HATS-647 / R0).

The value under test: an update never mutates the venv a live run is
executing from. ``self update`` on a managed default venv installs the new
version into ``versions/<sha>/`` and atomically flips ``versions/current``;
the previous ``versions/<old-sha>/`` is left untouched, so a concurrently
live run pinned to it keeps its frozen environment.

Setup contract (real subprocess + real pip + real launcher), per
``dev_rule_e2e_gate``:

  - ``src-repo``  — a clone of REPO_ROOT used as the local (non-editable)
    install source. Its HEAD sha names the installed version dir.
  - First ``self update`` → ``versions/<shaA>/`` + ``current → shaA``.
  - A trivial commit advances ``src-repo`` HEAD → ``shaB``.
  - Second ``self update`` → ``versions/<shaB>/`` + ``current → shaB``,
    while ``versions/<shaA>/`` survives unchanged.

Fail-under-revert: the pre-HATS-647 code installs in place into the single
``.venv`` and never creates ``versions/`` — so the ``versions/<sha>/`` +
``current`` assertions below fail, and ``versions/<shaA>/`` is never
preserved across the second update.

Pin-at-spawn details (a process pinned via ``AI_HATS_VENV`` stays on its
sha even after ``current`` flips; descendants inherit the pin) are covered
by the launcher unit tests in ``tests/test_launcher.py`` — this e2e proves
the real ``self update`` produces and advances the versioned layout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    """Run a subprocess; assert exit code matches ``expect_exit``."""
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


@pytest.mark.integration
def test_e2e_self_update_blue_green_versioned(tmp_path: Path) -> None:
    """A managed ``self update`` installs into versions/<sha> + flips current,
    and a second update to a new sha preserves the previous version dir."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # ----- fixture: local src-repo (the non-editable install source) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    _git(["config", "user.email", "e2e@test"], src_repo)
    _git(["config", "user.name", "E2E"], src_repo)
    sha_a = _head_sha(src_repo)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env.pop("AI_HATS_VENV", None)
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
    assert (vdir_a / "bin" / "ai-hats").is_file(), "versions/<shaA>/ venv incomplete"

    # Marker proving the old version dir is not rebuilt by the next update.
    marker = vdir_a / "AINTTOUCHED"
    marker.write_text("pinned\n")

    # ----- 2. advance src-repo HEAD → shaB (trivial, still installable) -----
    (src_repo / "E2E_VERSIONED_MARKER.txt").write_text("hats-647 e2e\n")
    _git(["add", "E2E_VERSIONED_MARKER.txt"], src_repo)
    _git(["commit", "--quiet", "-m", "test: advance HEAD for versioned e2e"], src_repo)
    sha_b = _head_sha(src_repo)
    assert sha_b != sha_a

    # ----- 3. second self update → versions/<shaB> + current flip -----
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    assert current.read_text().strip() == sha_b, "current did not flip to shaB"
    assert (versions / sha_b / "bin" / "ai-hats").is_file(), "versions/<shaB>/ missing"
    # AC1: the previously-pinned version dir survives the update untouched.
    assert vdir_a.is_dir(), "versions/<shaA>/ was destroyed by the update"
    assert marker.read_text() == "pinned\n", "versions/<shaA>/ was rebuilt in place"

    # ----- 4. the real launcher (no env) resolves the new current end-to-end -----
    clean = {k: v for k, v in env.items() if k != "AI_HATS_VENV"}
    _run([str(launcher_dest), "--help"], cwd=project, env=clean, timeout=60)
