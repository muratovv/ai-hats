"""e2e (HATS-649)

flow: a developer running self update when old version directories exist under versions/
cmds:
    ai-hats self update
expect: garbage collection prunes version directories older than retention threshold
why: without version garbage collection, accumulated version directories consume
     unbounded disk space"""

from __future__ import annotations
from _helpers.git import git

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = (
    pytest.mark.install_heavy
)  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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


def _lstart(pid: int) -> str:
    """The baseline as the READER renders it — same pinned TZ/locale.

    ``ps -o lstart=`` renders in the TZ and LC_TIME of the ``ps`` process, so a
    baseline planted under the ambient environment reads as a reused pid to a
    reader that pins its own — and the version a live ref pins gets reclaimed.
    """
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
    )
    return out.stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-649 e2e\n")
    git(src_repo, "add", marker)
    git(src_repo, "commit", "--quiet", "-m", f"test: advance HEAD ({marker})")
    return _head_sha(src_repo)


def _mk_complete(versions: Path, sha: str) -> Path:
    vdir = versions / sha
    (vdir / "bin").mkdir(parents=True)
    (vdir / "bin" / "ai-hats").write_text("#!/bin/sh\n")
    (vdir / ".complete").write_text("")
    return vdir


def _ref(refs: Path, sha: str, pid: int, start_time_utc: str, name: str) -> Path:
    import json

    f = refs / f"{name}.json"
    f.write_text(
        json.dumps({"run_id": name, "root_pid": pid, "start_time_utc": start_time_utc, "sha": sha})
    )
    return f


def _bootstrap(tmp_path: Path):
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
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    versions = project / ".agent" / "ai-hats" / "versions"
    return env, src_repo, launcher_dest, project, versions, sha_a


@pytest.mark.integration
def test_e2e_orphan_versions_reclaimed_by_liveness(tmp_path: Path) -> None:
    env, src_repo, launcher_dest, project, versions, sha_a = _bootstrap(tmp_path)

    # Second update: flip current → shaB so shaA becomes a non-current version.
    sha_b = _advance(src_repo, "E2E_R2_M1.txt")
    assert sha_b != sha_a
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    assert (versions / "current").read_text().strip() == sha_b

    refs = versions / ".refs"
    refs.mkdir(parents=True, exist_ok=True)

    sleeper = subprocess.Popen(["sleep", "600"])
    try:
        live_pid = sleeper.pid
        live_lstart = _lstart(live_pid)

        reaped = subprocess.Popen(["sleep", "30"])
        reaped.terminate()
        reaped.wait()
        dead_pid = reaped.pid

        # Plant three complete, non-current orphans with controlled refs.
        v_dead = _mk_complete(versions, "deada00000dead")
        _ref(refs, "deada00000dead", dead_pid, "Wed Jan  1 00:00:00 2000", "dead")
        v_reuse = _mk_complete(versions, "reuseb00000cafe")
        _ref(refs, "reuseb00000cafe", live_pid, "Wed Jan  1 00:00:00 2000", "reuse")
        v_live = _mk_complete(versions, "live0c00000beef")
        _ref(refs, "live0c00000beef", live_pid, live_lstart, "live")

        # Third update runs the reclaim at its start (current is still shaB here,
        # so shaB is protected; the planted orphans are evaluated by liveness).
        sha_c = _advance(src_repo, "E2E_R2_M2.txt")
        assert sha_c != sha_b
        _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

        assert not v_dead.exists(), "dead-ref orphan was not reclaimed (leak)"
        assert not v_reuse.exists(), "pid-reuse orphan was not reclaimed (leak)"
        assert v_live.is_dir(), "live-pinned version was wrongly reclaimed"
        # The dead/reuse refs are cleaned in the same pass; the live ref stays.
        assert not (refs / "dead.json").exists()
        assert not (refs / "reuse.json").exists()
        assert (refs / "live.json").exists()
        assert (versions / "current").read_text().strip() == sha_c
    finally:
        sleeper.kill()
        sleeper.wait()


def _git(args, cwd):
    return git(cwd, *args)
