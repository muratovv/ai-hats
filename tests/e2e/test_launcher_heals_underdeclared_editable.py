"""E2E: the launcher heals an editable install METADATA under-declares (HATS-1368).

Value under test: the launcher probes ``import ai_hats.cli`` before exec'ing,
and that import pulls in workspace members. On a venv whose editable METADATA
predates the workspace split the probe fails, so the launcher exits 1 **before**
the exec that would let the startup gate heal — and the hint it prints,
``ai-hats self update``, re-enters this same launcher and fails identically.
The gate fix alone (``python -m ai_hats``) does not reach this path, which is
the one consumers actually use.

Setup (real launcher + real venv + real uv, per ``dev_rule_e2e_gate``): install
the bash launcher, build an editable venv as a project's ``.agent/ai-hats/.venv``,
then strip the first-party ``Requires-Dist`` lines and the members they covered.

Assertion: ``ai-hats --version`` exits 0 — the launcher ran the bootstrap verify,
re-probed, and exec'd into a working CLI.

Fail-under-revert: drop the ``_bootstrap verify`` + re-probe branch in
``scripts/ai-hats-launcher`` and this exits 1 with "the ai-hats CLI is not
importable".
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.install_heavy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
def test_launcher_heals_an_editable_install_whose_metadata_underdeclares(tmp_path: Path) -> None:
    """The launcher's own import probe must not dead-end a healable venv."""
    from _helpers.editable_venv import (
        build_editable_venv,
        imports,
        make_metadata_predate_workspace_split,
    )
    from _helpers.venv import network_available, venv_unavailable

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build an editable venv")

    project = tmp_path / "project"
    (project / ".agent" / "ai-hats").mkdir(parents=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
    )
    launcher = tmp_path / "bin" / "ai-hats"
    launcher.parent.mkdir(parents=True)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(REPO_ROOT)
    env.pop(ENV_AI_HATS_VENV, None)  # a leaked session pin would be dropped as foreign
    env.pop("AI_HATS_DIR", None)
    env.pop("PYTHONPATH", None)

    try:
        subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "install-launcher.sh")],
            cwd=str(tmp_path),
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        venv_python, _ = build_editable_venv(
            project / ".agent" / "ai-hats", REPO_ROOT, venv_name=".venv"
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        venv_unavailable(f"launcher/venv build failed or timed out: {exc}")

    make_metadata_predate_workspace_split(venv_python)
    assert not imports(venv_python, "ai_hats_wt"), "setup failed — ai_hats_wt still importable"

    result = subprocess.run(
        [str(launcher), "--version"],
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, (
        f"launcher dead-ended on a healable venv (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert imports(venv_python, "ai_hats_wt"), "launcher exited 0 without healing the venv"
