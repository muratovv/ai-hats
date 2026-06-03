"""E2E: ``ai-hats self update`` skips pip install when installed SHA == remote.

The bug it catches:

  ``ai-hats self update`` unconditionally ran ``pip install --force-reinstall
  --no-cache-dir`` even when the installed HEAD already matched remote
  master. On slow links this added 10-60s of silent re-download per
  invocation. After the no-op short-circuit, the command reuses the
  HATS-432 ahead/behind probe and skips pip entirely when both sides
  resolve to the same SHA with ``ahead == 0`` and ``behind == 0``.

Setup contract (real subprocess + real pip):

  - ``src-repo``  — clone of REPO_ROOT, master tip becomes the installed
                    SHA after editable install.
  - ``fake-remote.git`` — bare clone of REPO_ROOT with master pinned to
                    the SAME SHA as src-repo's HEAD. The probe sees
                    ``installed_sha == latest_sha`` and ``(ahead, behind)
                    == (0, 0)``.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Fail-under-revert: if the short-circuit is removed from
``cli/maintenance.py``, the in-sync invocation runs pip and the
"skipping pip install" hint never prints — the assertion below fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
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


@pytest.mark.integration
def test_e2e_self_update_skips_pip_when_in_sync(tmp_path: Path) -> None:
    """End-to-end: in-sync state short-circuits the pip install dance.

    Two assertions amortize the heavy setup:

    1. ``self update`` (no flag) → exit 0 + "skipping pip install" hint.
       Proves the short-circuit fires inside a real subprocess.
    2. The same invocation completes well under the 60s timeout — pip
       install alone is typically 10-15s, so a regression that re-enables
       the unconditional pip would still complete but the hint check
       above guarantees fail-under-revert.
    """
    src_repo = tmp_path / "src-repo"
    fake_remote = tmp_path / "fake-remote.git"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # ----- fixture: src-repo (installed checkout) -----
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
    src_sha = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # ----- fixture: fake-remote.git (probe target — master pinned to same SHA) -----
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(REPO_ROOT), str(fake_remote)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fake_remote), "update-ref",
         "refs/heads/master", src_sha],
        check=True,
    )

    # ----- bootstrap: launcher + venv (uses src-repo for first install) -----
    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=180)

    # ----- convert to editable install so the package dir carries .git -----
    # Required for the ahead/behind probe to find a usable git checkout
    # reachable from ``ai_hats.__file__``. Same rationale as
    # test_self_update_downgrade_gate.py.
    venv_pip = project / ".agent" / "ai-hats" / ".venv" / "bin" / "pip"
    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    subprocess.run(
        [str(venv_pip), "uninstall", "-y", "--quiet", "ai-hats"],
        env=env, check=True, timeout=60,
    )
    subprocess.run(
        [str(venv_pip), "install", "--quiet", "-e", str(src_repo)],
        env=env, check=True, timeout=120,
    )
    # HATS-647: the non-editable bootstrap `self update` above created a
    # versions/<sha>/ + current pointer; drop it so the launcher resolves the
    # now-editable .venv via default precedence. This test exercises the
    # editable / legacy self-update path, not the versioned one.
    shutil.rmtree(project / ".agent" / "ai-hats" / "versions", ignore_errors=True)
    where = subprocess.run(
        [str(venv_python), "-c",
         "import ai_hats, pathlib; print(pathlib.Path(ai_hats.__file__).resolve())"],
        env=env, capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    assert str(src_repo) in where, (
        f"editable conversion did not take effect: ai_hats.__file__={where!r}"
    )

    # ----- give the project a default_role so the in-sync ``self update``
    # below actually re-assembles (HATS-407: the post-pip bump path skips
    # ``Re-assembling`` when both ``active_role`` and ``default_role`` are
    # empty — which they would be after a bare bootstrap install). Using
    # ``config set`` is the lightweight surface; no session start needed,
    # writes ``default_role`` into ai-hats.yaml, ``active_role`` stays
    # empty until first real session. -----
    _run(
        [str(launcher_dest), "config", "set", "-r", "assistant"],
        cwd=project, env=env, timeout=30,
    )

    # ----- swap probe target to the fake remote (master == src-repo HEAD) -----
    env["AI_HATS_REPO_URL"] = f"git+file://{fake_remote}"

    # ----- assertion 1: skip hint visible, no pip install ran -----
    started = time.monotonic()
    result = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=60,
    )
    elapsed = time.monotonic() - started
    combined = result.stdout + result.stderr
    assert "skipping pip install" in combined, (
        f"no-op short-circuit hint missing; combined output:\n{combined}"
    )
    assert "Already up to date" in combined, (
        f"already-up-to-date banner missing; combined output:\n{combined}"
    )
    # Bump still runs (migrations / hooks refresh); the "Re-assembling"
    # banner proves the in-process bump path executed.
    assert "Re-assembling" in combined, (
        f"bump did not run despite skipped pip; combined output:\n{combined}"
    )
    # ----- assertion 2: completes well under the 60s timeout -----
    # Cushion: pip install --force-reinstall --no-cache-dir alone takes
    # 10-15s in CI; if the short-circuit regresses, this still fits in 60s
    # but the hint assertion above already fires fail-under-revert. The
    # timing check is a defensive belt — flakes on overloaded CI would
    # only be a problem with a >30s budget.
    assert elapsed < 30.0, (
        f"in-sync self update took {elapsed:.1f}s — pip install likely "
        f"re-enabled by regression"
    )
