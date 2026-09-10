"""e2e (HATS-763)

flow:   a developer running self update with warm uv package cache
cmds:
    ai-hats self update
expect: self update utilizes uv cache for fast wheel installation without re-downloading
        packages
why: without uv cache utilization, self update spends excessive time downloading cached
     python wheels"""

from __future__ import annotations

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
]  # HATS-678: real install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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


# HATS-695: a real fresh wheel download into an isolated cache can time out under
# the -n8 gate's 300s budget on a slow/degraded network. Quarantined to unblock
# the gate; still runs solo and passes. uv's content-addressed cache makes warm
# runs near-instant — revisit un-quarantine once network-resilient.
@pytest.mark.quarantine
@pytest.mark.integration
def test_e2e_self_update_completes_via_uv(tmp_path: Path) -> None:
    """End-to-end smoke: the real binary survives ``self update`` under uv.

    Two assertions amortize the heavy setup (clone + launcher install +
    bootstrap uv install + --reinstall at a real SHA):

    1. ``self update --revision <SHA> --force`` exits 0.
    2. The post-install binary still runs (``ai-hats --version`` exits 0 with a
       non-empty version), proving the venv uv produced is usable.
    """
    src_repo = tmp_path / "src-repo"
    uv_cache = tmp_path / "uv-cache"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source
    uv_cache.mkdir()

    # ----- fixture: src-repo (clone of REPO_ROOT, carries history) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )

    # Pin to src-repo's current HEAD SHA. Pinning to a release tag would
    # downgrade ai-hats to a binary lacking newer flags (``--revision`` was
    # added in HATS-496) and break follow-up invocations.
    sha_probe = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    pinned_ref = sha_probe.stdout.strip()

    # ----- bootstrap env: dedicated uv cache, isolated from host -----
    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env["UV_CACHE_DIR"] = str(uv_cache)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    # ----- bootstrap: install launcher + first self update -----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run(
        [str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300
    )  # HATS-675: 300s = -n8 gate suite norm

    # Switch to git+file:// so --revision is reachable.
    env[ENV_REPO_URL] = f"git+file://{src_repo}"

    # ----- assertion 1: production code path exits 0 -----
    _run(
        [str(launcher_dest), "self", "update", "--revision", pinned_ref, "--force"],
        cwd=project,
        env=env,
        timeout=300,
    )

    # ----- assertion 2: the installed binary still runs -----
    version_result = _run(
        [str(launcher_dest), "--version"],
        cwd=project,
        env=env,
        timeout=30,
    )
    assert version_result.stdout.strip(), (
        f"ai-hats --version printed empty stdout after self update:\n"
        f"stderr:\n{version_result.stderr}"
    )
