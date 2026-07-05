"""E2E: Update banner fires for non-editable installs via probe-mirror (HATS-458).

The gap closed:

  HATS-432 added ahead/behind axes to ``CacheEntry`` so the banner only
  fires when ``behind > 0 and ahead == 0``. HATS-441 then tightened the
  probe to refuse foreign-``.git`` reads and fetches — required to stop a
  silent ``self update`` downgrade and prevent polluting user-project
  git histories. Side effect: non-editable installs (the typical
  launcher-bootstrap layout — ``pip install`` extracts ai_hats into
  ``site-packages/`` with no ``.git``) lost ahead/behind detection
  entirely. ``_fetch_into_pkg`` returned False → axes stayed None →
  banner silent even when a real update was available.

After HATS-458, ``run_check`` falls back to a persistent probe-mirror
at ``<ai_hats_dir>/.cache/probe-mirror/`` whenever the pkg-checkout
fast path can't run. This test exercises that fallback end-to-end:

  1. Bootstrap a real non-editable install (launcher pulls ai-hats from
     a clone reset to ``REPO_ROOT~5`` — a deterministic older state).
  2. Pin a fake remote's ``master`` to ``REPO_ROOT`` HEAD (5 commits
     ahead of installed).
  3. Confirm ``ai_hats.__file__`` lives in site-packages — the pkg-
     checkout fast path is structurally unreachable.
  4. Run the background probe entry-point ``python -m ai_hats.update_check``
     against the project.
  5. Assert the cache file records ``behind == 5, ahead == 0`` and a
     probe-mirror directory was created with at least one fetched ref.
  6. Render the banner via ``RenderUpdateBanner`` and assert it surfaces
     ``+5 commits`` on stderr.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``python -m ai_hats.update_check`` against a real bare remote. Marked
``@pytest.mark.integration``.

Fail-under-revert: reverting the mirror fallback in ``run_check`` leaves
``cache.behind = None``, which fails both the cache assertion and the
banner assertion below.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"
LAG_COMMITS = 5  # how many commits behind ``master`` the installed snapshot is


def _run(cmd, *, cwd, env, timeout, expect_exit=0, check_returncode=True):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if check_returncode and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_update_banner_fires_for_non_editable_install(tmp_path: Path) -> None:
    """End-to-end: non-editable install + behind remote → banner fires."""
    src_repo = tmp_path / "src-repo"
    fake_remote = tmp_path / "fake-remote.git"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    # ----- fixture: src-repo (installed) + fake-remote (LAG_COMMITS ahead) -----
    # The installed snapshot MUST be at the current worktree HEAD so the
    # venv carries the HATS-458 fix under test. We synthesize the "ahead"
    # remote by:
    #   1. Cloning the worktree → src-repo.
    #   2. Adding N empty commits to src-repo (the "future" master).
    #   3. Bare-cloning src-repo → fake-remote.git (master at HEAD+N).
    #   4. Resetting src-repo back to pre-empty-commits HEAD; the install
    #      sees this HEAD as ``__commit_id__`` (5 commits behind master).
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(src_repo), "config", "user.email", "e2e@test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(src_repo), "config", "user.name", "E2E"],
        check=True,
    )
    installed_sha = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    for i in range(LAG_COMMITS):
        subprocess.run(
            ["git", "-C", str(src_repo), "commit", "--allow-empty",
             "-m", f"HATS-458 e2e: synthetic ahead commit {i + 1}"],
            check=True,
        )
    ahead_sha = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(src_repo), str(fake_remote)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fake_remote), "update-ref",
         "refs/heads/master", ahead_sha],
        check=True,
    )

    # Reset src-repo back so the install snapshot is the *behind* commit.
    subprocess.run(
        ["git", "-C", str(src_repo), "reset", "--hard", installed_sha],
        check=True, capture_output=True, text=True,
    )

    # ----- bootstrap launcher + non-editable install from src-repo -----
    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    # The test runner sets PYTHONPATH=src to point at the worktree's
    # source; that would shadow the venv install and break the
    # non-editable-install premise.
    env.pop("PYTHONPATH", None)
    env.pop("AI_HATS_NO_UPDATE_CHECK", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=300)  # HATS-675: 300s = -n8 gate suite norm

    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert venv_python.is_file(), \
        f"project venv python missing at {venv_python}"

    # ----- guard: confirm ai_hats lives in site-packages (non-editable) -----
    where = subprocess.run(
        [str(venv_python), "-c",
         "import ai_hats, pathlib; "
         "print(pathlib.Path(ai_hats.__file__).resolve())"],
        env=env, capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    assert "site-packages" in where, (
        f"premise broken: ai_hats.__file__ is not in site-packages "
        f"(install was editable?). where={where!r}"
    )
    # The pkg-checkout fast path requires git ls-files --error-unmatch
    # __init__.py to succeed inside pkg_dir; for a non-editable install
    # in a project without ``git init``, no ``.git`` exists in any
    # ancestor of site-packages/ai_hats/ → fast path is structurally
    # unreachable. This is the regime HATS-458 targets.

    # ----- swap probe target + run background check ----
    env[ENV_REPO_URL] = f"git+file://{fake_remote}"
    _run(
        [str(venv_python), "-m", "ai_hats.update_check", str(project)],
        cwd=project, env=env, timeout=60,
    )

    # ----- assert mirror was used + cache reflects behind=LAG_COMMITS -----
    mirror = project / ".agent" / "ai-hats" / ".cache" / "probe-mirror"
    assert mirror.is_dir(), f"probe-mirror directory missing at {mirror}"
    assert (mirror / "HEAD").is_file(), \
        "probe-mirror was not initialized (HEAD missing)"

    cache_path = project / ".agent" / "ai-hats" / ".cache" / "update-check.json"
    assert cache_path.is_file(), f"cache file missing at {cache_path}"
    cache_data = json.loads(cache_path.read_text())
    # ``__commit_id__`` is a short SHA (9 chars); compare against the
    # known full SHA's prefix.
    assert cache_data["installed_sha"] and \
        installed_sha.startswith(cache_data["installed_sha"]), (
        f"cache installed_sha mismatch: expected prefix of {installed_sha!r}, "
        f"got {cache_data['installed_sha']!r}"
    )
    assert cache_data["latest_sha"] == ahead_sha, (
        f"cache latest_sha mismatch: expected {ahead_sha!r}, "
        f"got {cache_data['latest_sha']!r}"
    )
    assert cache_data["behind"] == LAG_COMMITS, (
        f"cache behind mismatch: expected {LAG_COMMITS}, "
        f"got {cache_data['behind']!r} (cache: {cache_data})"
    )
    assert cache_data["ahead"] == 0, (
        f"cache ahead mismatch: expected 0, "
        f"got {cache_data['ahead']!r} (cache: {cache_data})"
    )

    # ----- assert banner renders via RenderUpdateBanner -----
    banner = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env, timeout=15,
    )
    assert "ai-hats update available" in banner.stderr, (
        f"banner missing; stderr:\n{banner.stderr}"
    )
    assert f"+{LAG_COMMITS} commits" in banner.stderr, (
        f"banner lacks lag suffix; stderr:\n{banner.stderr}"
    )
