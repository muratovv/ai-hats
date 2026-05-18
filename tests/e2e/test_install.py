"""End-to-end smoke test for venv-first launcher install flow (HATS-333).

Catches integration bugs that unit tests miss because they stub the
launcher / pip / python (test_launcher.py, test_bootstrap_sh.py). Two
real bugs were found this way after the HATS-333 epic closed and would
have been masked by the stubbed unit suite:

  1. local-path AI_HATS_REPO_URL → pip rejected `ai-hats @ /path` (PEP
     508 requires URL scheme). Fixed in launcher + cli/maintenance.py.
  2. `ai-hats init` was already nested under `self` (HATS-242), but
     bootstrap.sh and docs still pointed at the top-level form which
     does not exist.

This test runs the **real** launcher, **real** pip install (from the
local repo path), **real** ai-hats commands. Slow (~60s on a warm pip
cache). Marked `integration` to be opted out via `pytest -m "not
integration"` when iterating.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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


@pytest.mark.integration
def test_e2e_install_init_break_heal(tmp_path):
    """Full venv-first lifecycle, end-to-end:

    1. install-launcher.sh → launcher binary in tmp.
    2. ai-hats self update → bootstraps default venv, installs ai-hats
       from the local repo (AI_HATS_REPO_URL = repo root).
    3. ai-hats self init -r assistant -p claude → yaml + composition.
    4. ai-hats config status → smoke pass.
    5. rm <venv>/bin/python → simulate proxmox python-upgrade case.
    6. ai-hats config status → exit 1 + actionable hint.
    7. ai-hats self update → heal-then-delegate: recreate venv + pip
       install + python rich self update + auto-bump.
    8. ai-hats config status → smoke pass again, composition restored.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)  # local install, no network for ai-hats itself
    env.pop("AI_HATS_VENV", None)  # never leak from outer test runs

    # ---- 1. install-launcher.sh ----
    res = _run(
        ["bash", str(INSTALL_LAUNCHER)],
        cwd=tmp_path, env=env, timeout=30,
    )
    assert launcher_dest.is_file(), f"launcher missing after install:\n{res.stdout}\n{res.stderr}"
    assert os.access(launcher_dest, os.X_OK), "launcher not executable"

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- 2. self update — bootstrap path ----
    res = ai_hats("self", "update")
    venv = project / ".agent" / "ai-hats" / ".venv"
    assert (venv / "bin" / "python").is_file(), "venv python missing after self update"
    assert (venv / "bin" / "ai-hats").is_file(), "ai-hats binary missing after install"
    # heal-then-delegate: python rich self update ran after bash heal.
    assert "Current version:" in res.stdout

    # ---- 3. self init ----
    res = ai_hats("self", "init", "-r", "assistant", "-p", "claude")
    assert (project / "ai-hats.yaml").is_file()
    assert (project / "CLAUDE.md").is_file()
    yaml_text = (project / "ai-hats.yaml").read_text()
    assert "active_role: assistant" in yaml_text
    assert "provider: claude" in yaml_text

    # ---- 4. composition smoke ----
    res = ai_hats("config", "status")
    assert "system_prompt: OK" in res.stdout

    # ---- 5. simulate broken venv (proxmox python-upgrade case) ----
    (venv / "bin" / "python").unlink()
    assert not (venv / "bin" / "python").exists()

    # ---- 6. broken command — actionable hint to stderr ----
    res = ai_hats("config", "status", expect_exit=1)
    assert "venv missing" in res.stderr
    assert "ai-hats self update" in res.stderr

    # ---- 7. self heal ----
    res = ai_hats("self", "update")
    assert "venv missing or broken — recreating" in res.stderr
    assert (venv / "bin" / "python").is_file(), "heal did not recreate python"
    # Full chain still works post-heal: rich UX from python self update + auto-bump.
    assert "Current version:" in res.stdout
    assert "Re-assembling: assistant" in res.stdout

    # ---- 8. composition restored ----
    res = ai_hats("config", "status")
    assert "system_prompt: OK" in res.stdout
