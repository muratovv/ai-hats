"""E2E (HATS-1125): Flag-only `self init` reconciles the venv to match the seeded harness channel.

Per `dev_rule_e2e_gate`: real subprocess + real uv + real launcher.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from ai_hats.constants import ENV_AI_HATS_INIT_SRC, ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.install_heavy

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
        stdin=subprocess.DEVNULL,  # non-TTY → self init takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_flag_only_init_reconciles_venv_channel(tmp_path: Path) -> None:
    """Flag-only `self init` reconciles the venv channel after seeding ai-hats.yaml."""
    import os

    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    import shutil

    shutil.copytree(
        REPO_ROOT,
        src_repo,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "build", "dist", "*.pyc"),
    )
    subprocess.run(["git", "init", "--quiet"], cwd=str(src_repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(src_repo), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"], cwd=str(src_repo), check=True
    )
    subprocess.run(["git", "add", "."], cwd=str(src_repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial", "--quiet"], cwd=str(src_repo), check=True)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop(ENV_AI_HATS_INIT_SRC, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    # Flag-only `self init` with explicit --channel local
    _run(
        [
            str(launcher_dest),
            "self",
            "init",
            "-r",
            "assistant",
            "-p",
            "claude",
            "--channel",
            "local",
            "--harness-path",
            str(src_repo),
        ],
        cwd=project,
        env=env,
        timeout=300,
    )

    cfg = project / PROJECT_CONFIG
    assert cfg.exists(), "self init did not write ai-hats.yaml"
    raw = yaml.safe_load(cfg.read_text())
    assert raw.get("harness", {}).get("channel") == "local"

    # Verify the project venv was reconciled to channel:local (-e / editable)
    proj_venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert proj_venv_python.exists(), "project venv interpreter missing"

    probe_code = (
        "import json, sys\n"
        "from importlib.metadata import distribution\n"
        'raw = distribution("ai-hats").read_text("direct_url.json") or "{}"\n'
        "info = json.loads(raw)\n"
        'is_editable = bool((info.get("dir_info") or {}).get("editable"))\n'
        'print("EDITABLE" if is_editable else "NOT_EDITABLE")\n'
    )
    res = _run([str(proj_venv_python), "-c", probe_code], cwd=project, env=env, timeout=30)
    assert res.stdout.strip() == "EDITABLE", (
        f"Expected venv to be reconciled to editable channel:local, but probe got: {res.stdout.strip()!r}"
    )
