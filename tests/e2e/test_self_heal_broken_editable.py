"""e2e (HATS-966, HATS-1367)

flow:   a developer whose workspace-member editable link points at a deleted path
cmds:
    ai-hats self heal-editables
expect: the heal re-points the dangling editable at the current repository path and
        the member imports again
why:    without broken editable healing, a moved or torn-down checkout leaves every
        launch dying on a ModuleNotFoundError the CLI itself cannot repair"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real uv install at call time → capped via conftest

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
        stdin=subprocess.DEVNULL,  # non-TTY
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _imports(vpy: Path, module: str, env) -> bool:
    return (
        subprocess.run(
            [str(vpy), "-c", f"import {module}"],
            env=env,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


@pytest.mark.integration
def test_e2e_heal_repoints_a_stale_workspace_editable(tmp_path: Path) -> None:
    """A dangling ``ai-hats-wt`` editable is re-pointed by ``self heal-editables``.

    Retargeted from the ``cline`` surface plugin by HATS-1826: the surfaces now
    ship inside ``ai-hats`` and have no editable of their own, so a ``packages/*``
    workspace member — the shape HATS-1367 widened this channel to — is what it
    still re-points.
    """
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
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
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    # self init builds the channel:local venv (ai-hats editable from src_repo,
    # and with it every packages/* workspace member).
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project,
        env=env,
        timeout=300,
    )

    venv = project / ".agent" / "ai-hats" / ".venv"
    vpy = venv / "bin" / "python"
    assert vpy.is_file(), "healed venv python missing"
    assert _imports(vpy, "ai_hats_wt", env), "ai_hats_wt should import after self init"

    # Break it: rewrite the editable .pth to a deleted path — the dangling state —
    # while leaving the canonical src_repo/packages/ai-hats-wt intact.
    pths = list((venv / "lib").glob("python*/site-packages/*ai_hats_wt*.pth"))
    assert pths, "ai_hats_wt editable .pth not found"
    pths[0].write_text("/tmp/gone-hats966-e2e/packages/ai-hats-wt/src\n")
    assert not _imports(vpy, "ai_hats_wt", env), "ai_hats_wt should be broken after the rewrite"

    result = _run(
        [str(vpy), "-m", "ai_hats", "self", "heal-editables"],
        cwd=project,
        env=env,
        timeout=180,
    )

    assert _imports(vpy, "ai_hats_wt", env), (
        "heal did not re-point the stale ai_hats_wt editable\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
