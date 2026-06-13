"""E2E: ``ai-hats self update`` exits non-zero when the install fails (HATS-718).

The bug it catches:

  In ``_run_managed_versioned_update`` the venv-create / pip-install / verify
  failure branches printed ``[red]Update failed[/]`` then did a bare ``return``
  — so click exited 0. A scripted chain (``ai-hats self update && ai-hats self
  init``), CI, or an agent reading exit codes could not distinguish a broken
  install from a successful one, and the ``&&`` chain proceeded to run ``init``
  against a half-updated environment. HATS-549 already fixed this class for the
  bump path (``sys.exit(1)``); this test pins the install-failure branches to
  the same contract.

Setup contract (real subprocess + real pip + real launcher), per
``dev_rule_e2e_gate``:

  - ``src-repo`` — a clone of REPO_ROOT used as the local (non-editable)
    install source. First ``self update`` → ``versions/<shaA>/`` + ``current``.
  - ``src-repo`` HEAD then advances to ``shaB`` whose working tree carries a
    DELIBERATELY BROKEN ``pyproject.toml`` (invalid TOML). git resolves shaB
    fine (it names the new version dir), but ``pip install <src-repo>`` fails
    to build it → the managed update's pip-install branch fires.
  - Second ``self update`` → exit 1, and ``versions/current`` is NOT flipped
    (still shaA), so the tool keeps running on the old, working version.

Fail-under-revert: with the ``462/474/485`` bare ``return``s restored, the
second update prints the red failure text but exits 0 — the ``expect_exit=1``
assertion below fails. (And because ``current`` is never flipped either way,
the half-updated environment is what the pre-fix ``&&`` chain would have run
``init`` against.)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.pip_heavy  # HATS-678: real pip at call time → capped via conftest.PIP_HEAVY_GROUPS


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
def test_e2e_self_update_install_failure_exits_nonzero(tmp_path: Path) -> None:
    """A managed ``self update`` whose pip install fails to build exits 1 and
    leaves ``versions/current`` pinned to the previous (working) sha."""
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
    assert current.read_text().strip() == sha_a, "first update did not pin current → shaA"

    # ----- 2. advance src-repo HEAD → shaB with a BROKEN pyproject.toml -----
    # git resolves shaB fine (names the new version dir), but the working tree
    # pip builds is no longer installable → the pip-install branch fires.
    (src_repo / "pyproject.toml").write_text(
        "this is not valid TOML @@@ [[[ HATS-718 broken build\n"
    )
    _git(["add", "pyproject.toml"], src_repo)
    _git(["commit", "--quiet", "-m", "test: break pyproject so pip install fails"], src_repo)
    sha_b = _head_sha(src_repo)
    assert sha_b != sha_a

    # ----- 3. second self update → install build fails → exit 1, current stays -----
    failed = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=300, expect_exit=1,
    )
    combined = failed.stdout + failed.stderr
    assert "Update failed" in combined, (
        f"missing failure text; combined output:\n{combined}"
    )
    # AC: current is NOT flipped — the tool still resolves the old, working sha.
    assert current.read_text().strip() == sha_a, (
        f"current flipped to a half-installed version: {current.read_text().strip()!r}"
    )
    assert (versions / sha_a / "bin" / "ai-hats").is_file(), \
        "previous working version dir was damaged by the failed update"
