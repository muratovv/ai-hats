"""E2E: ``ai-hats self update`` on the ``local`` channel is an editable
in-place reinstall (HATS-764).

Value under test: a project pinned ``harness.channel: local`` installs its
working tree with ``uv pip install -e <path>`` IN PLACE — no ``versions/<sha>/``
blue-green dir, and the resulting install is editable (PEP 610
``dir_info.editable == true``). This is the ai-hats-dev dogfooding path: the
launcher tracks the working tree instead of a frozen managed venv.

Fail-under-revert: the pre-HATS-764 code has no channel model — it strips the
unknown ``harness`` block and falls through to the managed/in-place git install,
which (a) creates a ``versions/`` dir and/or (b) leaves a NON-editable install.
Both assertions below then fail.

Setup contract (real subprocess + real uv + real launcher), per
``dev_rule_e2e_gate``.
"""

from __future__ import annotations

import json
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
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_self_update_local_editable_in_place(tmp_path: Path) -> None:
    """channel: local → editable reinstall in place, no versioned dir."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # The local channel installs `-e <path>`; point it at a real checkout.
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)

    # Pin channel: local at the cloned src checkout.
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
    env[ENV_REPO_URL] = str(src_repo)  # launcher bootstrap source
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    ai_hats_dir = project / ".agent" / "ai-hats"
    # 1. No versioned blue-green dir for an editable local install.
    assert not (ai_hats_dir / "versions").exists(), (
        "local channel must NOT create versions/ — editable installs are in place"
    )
    # 2. The managed .venv install is editable (PEP 610 dir_info.editable).
    venv = ai_hats_dir / ".venv"
    # HATS-790: no bin/ai-hats console script — usability is bin/python; assert
    # the proxy binary is ABSENT (editable install must not re-introduce it).
    assert (venv / "bin" / "python").is_file(), "managed .venv python missing"
    assert not (venv / "bin" / "ai-hats").exists(), (
        "bin/ai-hats console script must NOT exist (HATS-790)"
    )
    dist_info = list((venv / "lib").glob("python*/site-packages/ai_hats-*.dist-info"))
    assert dist_info, "ai-hats dist-info not found in .venv"
    direct_url = json.loads((dist_info[0] / "direct_url.json").read_text())
    assert direct_url.get("dir_info", {}).get("editable") is True, (
        f"local channel install is not editable: {direct_url}"
    )

    # 3. The launcher resolves the editable install end-to-end (no env pin).
    clean = {k: v for k, v in env.items() if k != ENV_AI_HATS_VENV}
    _run([str(launcher_dest), "--help"], cwd=project, env=clean, timeout=60)
