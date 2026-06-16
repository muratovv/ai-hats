"""E2E: a crashed session never leaks disk (HATS-649 / R2).

Value under test: ``versions/<sha>/`` dirs orphaned by an ended/crashed run are
reclaimed on the next ``ai-hats`` invocation, while a version a **live** run is
pinned to is never touched. Exercised with a real launcher + real pip + real
``ai-hats self update`` (per ``dev_rule_e2e_gate``) and real OS pids — no flaky
SIGKILL race: liveness is decided deterministically by ``root_pid`` +
``ps``-reported ``start_time``.

Three planted complete, non-``current`` versions, each with a liveness ref:
  1. **dead pid** — a spawned-then-reaped pid → reclaimed.
  2. **pid reuse** — a *live* pid but a non-matching ``start_time`` → reclaimed
     (precise reuse detection, no TTL).
  3. **live pin** — a *live* pid with the correct ``start_time`` → kept.

Fail-under-revert:
  - reverting the reclaim → cases 1 & 2 ``not exists`` assertions fail (leak);
  - reverting the liveness keep (treat all as dead) → case 3's ``is_dir``
    assertion fails (a live run's env wrongly deleted).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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


def _lstart(pid: int) -> str:
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
    )
    return out.stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-649 e2e\n")
    _git(["add", marker], src_repo)
    _git(["commit", "--quiet", "-m", f"test: advance HEAD ({marker})"], src_repo)
    return _head_sha(src_repo)


def _mk_complete(versions: Path, sha: str) -> Path:
    vdir = versions / sha
    (vdir / "bin").mkdir(parents=True)
    (vdir / "bin" / "ai-hats").write_text("#!/bin/sh\n")
    (vdir / ".complete").write_text("")
    return vdir


def _ref(refs: Path, sha: str, pid: int, start_time: str, name: str) -> Path:
    import json
    f = refs / f"{name}.json"
    f.write_text(json.dumps(
        {"run_id": name, "root_pid": pid, "start_time": start_time, "sha": sha}
    ))
    return f


def _bootstrap(tmp_path: Path):
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
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop("AI_HATS_VENV", None)
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
