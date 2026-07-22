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

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.project import pin_edge_channel  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402

# HATS-685: build the subprocess env without inherited PYTHONPATH/redirect vars
# so the real-pip install is exercised, not the source tree.
from _helpers.env import clean_env  # noqa: E402
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG  # noqa: E402
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL  # noqa: E402

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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
    5. rm python from BOTH the active versions/<sha>/ venv AND .venv →
       simulate a host python upgrade (proxmox case). Post-HATS-647 the
       active venv is versions/<sha>, so breaking only .venv is a no-op;
       a real python upgrade breaks every venv's hardcoded interpreter.
    6. ai-hats config status → exit 1 + actionable hint. The launcher
       routes the python-broken versioned venv to .venv (also broken,
       HATS-656), so the exec check reports the missing interpreter.
    7. ai-hats self update → heal-then-delegate: the launcher falls back to
       the (broken) default .venv, heal recreates it, and the python rich
       self update + auto-bump restore the tool. HATS-657: read_current_sha
       treats the python-broken versioned install as unusable, so the update
       REBUILDS versions/<sha> (not already_current) AND stays silent on the
       HATS-655 dormancy advisory (the launcher correctly skipped a broken
       venv — it is not stale).
    8. ai-hats config status → smoke pass again, composition restored.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    src_repo = tmp_path / "src-repo"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    # Install source: a standalone full clone of the repo under test (NOT
    # build_src / REPO_ROOT directly). A standalone clone's HEAD is unresolvable
    # against GitHub origin/master in the update-check probe, so the HATS-441
    # ahead-of-origin downgrade guard stays inactive while these commits are
    # unpushed — matching the sibling versioned e2e tests. build_src shares
    # REPO_ROOT's object store, which lets the probe resolve "ahead" and refuse
    # the step-7 heal self update. The per-test clone also owns its own build/
    # dir, so concurrent xdist workers never race the in-tree wheel build.
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    subprocess.run(["git", "-C", str(src_repo), "config", "user.email", "e2e@test"],
                   check=True)
    subprocess.run(["git", "-C", str(src_repo), "config", "user.name", "E2E"],
                   check=True)

    env = clean_env()  # HATS-685: drop inherited PYTHONPATH/redirect vars
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)  # local install, no network for ai-hats itself
    env.pop(ENV_AI_HATS_VENV, None)  # never leak from outer test runs

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
    # HATS-675: real-pip install via the generic helper — override the
    # 180s default to the 300s -n8 gate suite norm (the helper default
    # stays 180 for the no-pip self init / config status calls below).
    res = ai_hats("self", "update", timeout=300)
    venv = project / ".agent" / "ai-hats" / ".venv"
    assert (venv / "bin" / "python").is_file(), "venv python missing after self update"
    # HATS-790: no bin/ai-hats console script — assert NO proxy binary AND that
    # the package is importable via the venv interpreter (the real install signal
    # the launcher now probes with `python -c "import ai_hats"`).
    assert not (venv / "bin" / "ai-hats").exists(), (
        "bin/ai-hats console script must NOT exist after install (HATS-790)"
    )
    import_probe = subprocess.run(
        [str(venv / "bin" / "python"), "-c", "import ai_hats"],
        capture_output=True, text=True,
    )
    assert import_probe.returncode == 0, (
        f"ai_hats not importable after install: {import_probe.stderr}"
    )
    # heal-then-delegate: python rich self update ran after bash heal.
    assert "Current version:" in res.stdout

    # ---- 3. self init ----
    res = ai_hats("self", "init", "-r", "assistant", "-p", "claude")
    assert (project / PROJECT_CONFIG).is_file()
    assert (project / "CLAUDE.md").is_file()
    yaml_text = (project / PROJECT_CONFIG).read_text()
    # HATS-407: init writes default_role; active_role stays empty
    # (runtime cache, written by session-bootstrap only).
    assert "default_role: assistant" in yaml_text
    assert "provider: claude" in yaml_text

    # ---- 4. composition smoke ----
    res = ai_hats("config", "status")
    assert "system_prompt: OK" in res.stdout

    # ---- 5. simulate a host python upgrade — breaks BOTH venvs ----
    # Post-HATS-647 the active venv is versions/<sha>, not .venv. A python
    # upgrade breaks every venv's hardcoded interpreter, so break python in
    # the active versioned venv AND the legacy .venv (HATS-656 — breaking
    # only .venv leaves the tool running fine from the versioned venv).
    versions = project / ".agent" / "ai-hats" / "versions"
    active_sha = (versions / "current").read_text().strip()
    active_python = versions / active_sha / "bin" / "python"
    assert active_python.is_file(), "expected an active versioned venv after step 2"
    active_python.unlink()
    (venv / "bin" / "python").unlink()
    assert not active_python.exists()
    assert not (venv / "bin" / "python").exists()

    # ---- 6. broken command — actionable hint to stderr ----
    # HATS-656 / HATS-790: the launcher requires an executable bin/python to
    # select a versioned venv (no bin/ai-hats console script exists), so the
    # python-broken versioned venv routes to the (also broken) .venv and the
    # exec check reports the missing interpreter.
    res = ai_hats("config", "status", expect_exit=1)
    assert "venv missing" in res.stderr
    assert "ai-hats self update" in res.stderr

    # ---- 7. self heal — fall back to .venv, recreate it, restore the tool ----
    # HATS-675: real-pip heal install — override the 180s helper default
    # to the 300s -n8 gate suite norm (see step 2).
    res = ai_hats("self", "update", timeout=300)
    assert "venv missing or broken" in res.stderr
    assert "interpreter missing at" in res.stderr
    assert "recreating" in res.stderr
    assert (venv / "bin" / "python").is_file(), "heal did not recreate .venv python"
    # Full chain runs post-heal: rich UX from python self update + auto-bump.
    assert "Current version:" in res.stdout
    assert "Re-assembling: assistant" in res.stdout
    # HATS-657 #1: the python-broken versioned install is unusable
    # (read_current_sha → None), so the update is NOT already_current and the
    # reuse gate refuses the broken dir — versions/<sha> is REBUILT with a fresh
    # interpreter rather than skipped.
    rebuilt_python = versions / (versions / "current").read_text().strip() / "bin" / "python"
    assert rebuilt_python.is_file(), (
        "HATS-657: versioned venv was not rebuilt after the python upgrade\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # HATS-657 #2: the dormancy advisory must NOT false-fire here — the launcher
    # correctly skipped a BROKEN versioned venv, it does not predate the layout.
    combined = res.stdout + res.stderr
    assert "host launcher is not using the versioned" not in combined, (
        "HATS-657: dormancy advisory false-fired on a python-broken heal\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )

    # ---- 8. tool restored — runs again (from the recreated .venv) ----
    res = ai_hats("config", "status")
    assert "system_prompt: OK" in res.stdout


@pytest.mark.integration
def test_e2e_fresh_init_heals(tmp_path):
    """Fresh project: `ai-hats self init` works as the FIRST command, with no
    prior `self update` (HATS-612).

    Before HATS-612 the launcher only healed the default venv for `self
    update`; a fresh-project `self init` was rejected with `Run: ai-hats self
    update`. Now `self init` triggers the same heal, so a single command
    creates the venv + configures the project.

    Real launcher + real pip (local repo) + real ai-hats binary. Fail-under-
    revert: with the launcher heal-on-init edit removed, step 2 exits 1 with
    the venv-missing rejection instead of writing ai-hats.yaml.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = clean_env()  # HATS-685: drop inherited PYTHONPATH/redirect vars
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))  # local install, no network
    env.pop(ENV_AI_HATS_VENV, None)

    # ---- 1. install launcher ----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    assert launcher_dest.is_file() and os.access(launcher_dest, os.X_OK)

    # ---- 2. self init as the first command — no `self update` first ----
    venv = project / ".agent" / "ai-hats" / ".venv"
    assert not venv.exists(), "precondition: fresh project has no venv yet"
    res = _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=180, expect_exit=0,
    )

    # Heal ran (launcher recreated the default venv before delegating to init).
    assert "recreating" in res.stderr, f"heal-on-init did not run:\n{res.stderr}"
    assert (venv / "bin" / "python").is_file(), "venv python missing after self init"
    # HATS-790: no bin/ai-hats console script — the install signal is importability.
    assert not (venv / "bin" / "ai-hats").exists(), (
        "bin/ai-hats console script must NOT exist after self init (HATS-790)"
    )
    init_probe = subprocess.run(
        [str(venv / "bin" / "python"), "-c", "import ai_hats"],
        capture_output=True, text=True,
    )
    assert init_probe.returncode == 0, (
        f"ai_hats not importable after self init: {init_probe.stderr}"
    )

    # Init configured the project in the same command.
    assert (project / PROJECT_CONFIG).is_file()
    assert (project / "CLAUDE.md").is_file()
    yaml_text = (project / PROJECT_CONFIG).read_text()
    assert "default_role: assistant" in yaml_text
    assert "provider: claude" in yaml_text
