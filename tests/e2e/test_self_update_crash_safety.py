"""E2E: an interrupted ``self update`` never bricks the tool (HATS-648 / R1).

Value under test: ``kill`` / Ctrl-C mid-``self update`` → the next launch still
works, ``versions/current`` is always valid, and incomplete residue does not
leak. Two deterministic post-crash states are exercised with a real subprocess
+ real pip + real launcher (per ``dev_rule_e2e_gate``) — no flaky SIGKILL race:

  1. **sentinel-on-success** — a real managed install writes the ``.complete``
     sentinel into ``versions/<sha>/``. The sentinel is the completeness
     authority ``read_current_sha`` gates on, written only after install+verify.
  2. **no-leak sweep** — an aged incomplete ``versions/<sha>/`` (the residue a
     crashed install leaves: a dir without ``.complete``) is reclaimed by the
     next real ``ai-hats`` invocation, while a *recent* incomplete dir (a
     possible install in flight), the complete previous version, and
     ``current`` are all left untouched.

Fail-under-revert:
  - reverting the sentinel write → test 1's ``.complete`` assertion fails;
  - reverting the recovery sweep → test 2's aged-residue ``not exists``
    assertion fails (the leak persists).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel

pytestmark = pytest.mark.pip_heavy  # HATS-678: real pip at call time → capped via conftest.PIP_HEAVY_GROUPS


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


def _bootstrap(tmp_path: Path):
    """Install the launcher + first managed self update; return (env, paths)."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    _git(["config", "user.email", "e2e@test"], src_repo)
    _git(["config", "user.name", "E2E"], src_repo)
    _git(["checkout", "-B", "e2e-main"], src_repo)  # HATS-764: align ls-remote HEAD
    sha_a = _head_sha(src_repo)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")  # deterministic discard
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    versions = project / ".agent" / "ai-hats" / "versions"
    return env, src_repo, launcher_dest, project, versions, sha_a


@pytest.mark.integration
def test_e2e_self_update_writes_complete_sentinel(tmp_path: Path) -> None:
    """A fully-successful managed install marks the version dir complete."""
    env, _src, launcher_dest, project, versions, sha_a = _bootstrap(tmp_path)

    assert (versions / "current").read_text().strip() == sha_a
    # HATS-648: completeness is the .complete sentinel, written last.
    assert (versions / sha_a / ".complete").is_file(), \
        "versions/<shaA>/.complete sentinel not written on a successful install"
    # The launcher resolves the complete current end-to-end (no env pin).
    clean = {k: v for k, v in env.items() if k != "AI_HATS_VENV"}
    _run([str(launcher_dest), "--help"], cwd=project, env=clean, timeout=60)


@pytest.mark.integration
def test_e2e_self_update_reclaims_incomplete_residue(tmp_path: Path) -> None:
    """The next ``self update`` sweeps aged incomplete residue but keeps a recent
    incomplete dir, the complete previous version, and ``current``."""
    env, src_repo, launcher_dest, project, versions, sha_a = _bootstrap(tmp_path)

    # Plant crash residue: an AGED incomplete dir (no .complete) + a RECENT one.
    aged = versions / "aaaaaaa0deadbeef"
    (aged / "bin").mkdir(parents=True)
    (aged / "bin" / "ai-hats").write_text("#!/bin/sh\n")
    old = time.time() - 48 * 3600
    os.utime(aged, (old, old))

    recent = versions / "bbbbbbb1cafef00d"
    (recent / "bin").mkdir(parents=True)
    (recent / "bin" / "ai-hats").write_text("#!/bin/sh\n")

    # Advance src-repo → shaB so the next update performs a real install (its
    # eager sweep runs first).
    (src_repo / "E2E_R1_MARKER.txt").write_text("hats-648 e2e\n")
    _git(["add", "E2E_R1_MARKER.txt"], src_repo)
    _git(["commit", "--quiet", "-m", "test: advance HEAD for R1 e2e"], src_repo)
    sha_b = _head_sha(src_repo)
    assert sha_b != sha_a

    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    # Aged incomplete residue reclaimed; recent incomplete kept (TTL guard).
    assert not aged.exists(), "aged incomplete residue was not reclaimed (leak)"
    assert recent.is_dir(), "recent incomplete dir wrongly swept (no TTL guard)"
    # Complete previous version + the new current are untouched.
    assert (versions / sha_a / ".complete").is_file(), "complete shaA was touched"
    assert (versions / "current").read_text().strip() == sha_b
    assert (versions / sha_b / ".complete").is_file()
