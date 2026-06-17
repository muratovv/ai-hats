"""End-to-end coverage for the Update banner pipeline (HATS-401).

Per ``dev_rule_e2e_gate`` — the trigger surface (new ``python -m
ai_hats.update_check`` entry-point + two new pipeline steps wired into
``execute.yaml`` / ``human.yaml`` / ``presets.py``) requires a real
subprocess chain. This test runs the real launcher install + real pip
install + real ai-hats binary, then exercises the installed package via
the venv'd Python — the same chain users hit when they run
``ai-hats execute``.

Why this lives in ``tests/e2e/`` and not ``tests/pipeline/``: the wiring
unit tests assert step IDs on the in-process pipeline; this test asserts
the chain actually works inside a freshly installed venv — catching e.g.
``__main__.py`` not being shipped by the wheel, or the YAML loader picking
the wrong registration order.

Revert-check: removing the ``render_update_banner`` registration from
``pipeline/steps/__init__.py`` makes ``test_render_step_emits_banner``
fail with an ``ImportError``; reverting the YAML edits makes
``test_execute_yaml_carries_update_steps`` fail with the wrong step list.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.repo_src import build_src  # noqa: E402

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS

# HATS-782: the installed sha is detected at runtime (the real running build) so
# HATS-781's installed-SHA banner guard does not suppress the seeded banner.
# LATEST_SHA is the synthetic "remote tip" — the display target, never compared
# to the running sha.
LATEST_SHA = "9876543210fedcba9876543210fedcba98765432"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _seed_cache(
    project: Path,
    *,
    installed: str,
    latest: str,
    behind: int | None = None,
    ahead: int | None = None,
    installed_label: str | None = None,
    latest_label: str | None = None,
) -> Path:
    cache = project / ".agent" / "ai-hats" / ".cache" / "update-check.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "installed_sha": installed,
        "latest_sha": latest,
        "remote_url": "https://example.git",
        "behind": behind,
        "ahead": ahead,
        "installed_label": installed_label,
        "latest_label": latest_label,
    }) + "\n")
    return cache


@pytest.mark.integration
def test_update_banner_e2e(tmp_path):
    """Full real-subprocess lifecycle for the Update banner pipeline.

    Stages:
      1. install-launcher.sh → launcher binary.
      2. ai-hats self update → real pip install from REPO_ROOT.
      3. ai-hats self init → ``ai-hats.yaml`` + composition.
      4. Inspect ``execute.yaml`` via the installed loader — assert both
         update-check steps are present in the right order.
      5. Pre-seed cache with strictly-behind state (behind=19, ahead=0),
         run ``RenderUpdateBanner`` via venv python — assert banner on
         stderr + ``+19 commits`` suffix + opt-out hint visible.
      5b. HATS-432 regression — installed ahead of upstream
          (behind=0, ahead=5), assert silent.
      5c. Diverged — both sides carry unique commits
          (behind=3, ahead=2), assert silent.
      6. Strictly-behind cache with ``AI_HATS_NO_UPDATE_CHECK=1`` —
         assert silent.
      7. Identical tips (behind=ahead=0) — assert silent.
      8. Verify ``python -m ai_hats.update_check`` exists as a runnable
         module (missing-arg → exit 1).
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(build_src(REPO_ROOT))
    env.pop("AI_HATS_VENV", None)
    env.pop("AI_HATS_NO_UPDATE_CHECK", None)

    # ---- 1. install launcher ----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    assert launcher_dest.is_file()

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 2. self update — pip-installs ai-hats from local repo ----
    ai_hats("self", "update")
    venv = project / ".agent" / "ai-hats" / ".venv"
    venv_python = venv / "bin" / "python"
    assert venv_python.is_file()

    # HATS-782: seed the cache with the REAL installed sha. HATS-781 added a
    # RenderUpdateBanner guard that suppresses a banner whose cached
    # installed_sha does not match detect_installed_sha() of the running build
    # ("never advertise an update about a build we are not running"). Seeding a
    # synthetic sha (the pre-781 approach) now hides EVERY banner — so query the
    # venv's own detector (the same interpreter the banner step runs under) and
    # seed THAT, so each stage exercises its intended behind/ahead path rather
    # than the sha-mismatch guard.
    real_sha = _run(
        [str(venv_python), "-c",
         "from ai_hats.update_check import detect_installed_sha; "
         "print(detect_installed_sha() or '')"],
        cwd=project, env=env, timeout=15,
    ).stdout.strip()
    assert real_sha, "detect_installed_sha() empty — cannot seed a matching cache"

    # ---- 3. self init ----
    ai_hats("self", "init", "-r", "assistant", "-p", "claude")
    assert (project / "ai-hats.yaml").is_file()

    # ---- 4. YAML wiring visible from the installed loader ----
    # The loader's `__main__` prints the resolved step graph; that
    # exercises the real registry + real YAML in one shot.
    res = _run(
        [str(venv_python), "-m", "ai_hats.pipeline.loader",
         str(REPO_ROOT / "library" / "core" / "pipelines" / "execute.yaml")],
        cwd=project, env=env, timeout=30,
    )
    assert "check_update_async" in res.stdout, res.stdout
    assert "render_update_banner" in res.stdout, res.stdout
    # Order check: check_update_async is step 1, render_update_banner is last.
    lines = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("1.") or
             ln.strip().startswith(("2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))]
    assert lines, f"no numbered step lines in output:\n{res.stdout}"
    assert "check_update_async" in lines[0], lines
    assert "render_update_banner" in lines[-1], lines

    # ---- 5. seed strictly-behind → banner renders with +N suffix ----
    _seed_cache(
        project,
        installed=real_sha,
        latest=LATEST_SHA,
        behind=19,
        ahead=0,
    )
    res = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env, timeout=15,
    )
    assert "ai-hats update available" in res.stderr, res.stderr
    assert real_sha[:7] in res.stderr, res.stderr
    assert LATEST_SHA[:7] in res.stderr, res.stderr
    assert "+19 commits" in res.stderr, res.stderr
    assert "ai-hats self update" in res.stderr, res.stderr
    # Discoverability of opt-out — third dim line.
    assert "AI_HATS_NO_UPDATE_CHECK" in res.stderr, res.stderr

    # ---- 5b. HATS-432 regression: installed AHEAD of upstream → silent ----
    # The cache claims a "latest" SHA but installed has commits past it. The
    # old `installed_sha != latest_sha` check fired here (arrow backwards in
    # time); the ahead/behind semantics must suppress.
    _seed_cache(
        project,
        installed=real_sha,
        latest=LATEST_SHA,
        behind=0,
        ahead=5,
    )
    res = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env, timeout=15,
    )
    assert "ai-hats update available" not in res.stderr, res.stderr

    # ---- 5c. diverged (both sides have unique commits) → silent ----
    _seed_cache(
        project,
        installed=real_sha,
        latest=LATEST_SHA,
        behind=3,
        ahead=2,
    )
    res = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env, timeout=15,
    )
    assert "ai-hats update available" not in res.stderr, res.stderr

    # ---- 6. opt-out — strictly-behind cache, banner suppressed by env var ----
    _seed_cache(
        project,
        installed=real_sha,
        latest=LATEST_SHA,
        behind=19,
        ahead=0,
    )
    env_optout = env.copy()
    env_optout["AI_HATS_NO_UPDATE_CHECK"] = "1"
    res = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env_optout, timeout=15,
    )
    assert "ai-hats update available" not in res.stderr, res.stderr

    # ---- 7. matching SHAs (behind=ahead=0) — silent ----
    _seed_cache(
        project,
        installed=real_sha,
        latest=real_sha,
        behind=0,
        ahead=0,
    )
    res = _run(
        [str(venv_python), "-c",
         "import sys; from pathlib import Path; "
         "from ai_hats.pipeline.steps.update_banner import RenderUpdateBanner; "
         "RenderUpdateBanner().run(project_dir=Path(sys.argv[1]))",
         str(project)],
        cwd=project, env=env, timeout=15,
    )
    assert "ai-hats update available" not in res.stderr, res.stderr

    # ---- 8. background entry-point exists ----
    # Without args the entry-point must exit 1 (no project_dir given).
    # This proves __main__.py shipped with the wheel — would fail
    # `ModuleNotFoundError` if the file were dropped.
    res = _run(
        [str(venv_python), "-m", "ai_hats.update_check"],
        cwd=project, env=env, timeout=15, expect_exit=1,
    )
