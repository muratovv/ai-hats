"""E2E: launcher heal detects an editable venv missing a workspace member (HATS-895).

Incident replica: bare ``import ai_hats`` passes on a venv whose editable link
moved ahead of its installed deps, so the old probe skipped the heal and even
``self update`` crashed at import. Value under test: the deep probe makes
``self update`` heal the mined venv, and the heal's ``uv pip install -e <root>``
restores EVERY ``packages/*`` member editable (uv resolves ``[tool.uv.sources]``
— the F2 contract, locked here, not re-implemented in the launcher).

Fail-under-revert: with the bare probe the heal is skipped → the launcher execs
the broken CLI → ``self update`` exits non-zero. Real subprocess + real uv +
real launcher per ``dev_rule_e2e_gate``; incident details → task card HATS-895.
"""
# comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e._helpers.workspace import workspace_members
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real uv installs at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY → self init takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_self_update_heals_venv_missing_workspace_member(tmp_path: Path) -> None:
    """Mined venv (member uninstalled) + ``self update`` → healed, all members editable."""
    src_repo = tmp_path / "src-repo"
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=300,
    )

    venv = project / ".agent" / "ai-hats" / ".venv"
    py = venv / "bin" / "python"
    members = workspace_members(src_repo)
    assert members, "workspace has no packages/* members — test premise broken"

    # Mine the venv exactly like the incident: the member IS in the CLI import
    # chain, so the CLI is dead while bare `import ai_hats` still passes.
    _run(
        ["uv", "pip", "uninstall", "--python", str(py), "ai-hats-core"],
        cwd=tmp_path, env=env, timeout=60,
    )
    bare = subprocess.run([str(py), "-c", "import ai_hats"], capture_output=True, text=True)
    assert bare.returncode == 0, (
        f"premise broken: bare import must still pass on the mined venv\n{bare.stderr}"
    )
    deep = subprocess.run([str(py), "-c", "import ai_hats.cli"], capture_output=True, text=True)
    assert deep.returncode != 0, "premise broken: mined venv should fail the deep import"

    # Act: the one command a user reaches for — must heal, not crash.
    _run([str(launcher), "self", "update"], cwd=project, env=env, timeout=600)

    # Deep import green, including every workspace member.
    mods = ", ".join(["ai_hats.cli", *(imp for _, imp in members)])
    probe = subprocess.run([str(py), "-c", f"import {mods}"], capture_output=True, text=True)
    assert probe.returncode == 0, f"healed venv still broken: {probe.stderr}"

    # R2 lock: every packages/* member is installed EDITABLE by the heal.
    site = next((venv / "lib").glob("python*/site-packages"))
    for dist, _imp in members:
        infos = list(site.glob(f"{dist.replace('-', '_')}-*.dist-info"))
        assert infos, f"{dist} not installed after heal"
        direct_url = json.loads((infos[0] / "direct_url.json").read_text())
        assert direct_url.get("dir_info", {}).get("editable") is True, (
            f"{dist} not editable after heal: {direct_url}"
        )
