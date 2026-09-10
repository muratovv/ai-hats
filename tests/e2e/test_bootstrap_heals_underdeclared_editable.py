"""e2e (HATS-1556)

flow:   a developer running ai-hats when editable install metadata under-declares deps
cmds:
    python -m ai_hats --version
expect: startup gate detects missing dependencies from pyproject.toml and heals editable
        install
why:    without live pyproject inspection, stale metadata causes module import crashes
        at runtime
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
def test_gate_heals_an_editable_install_whose_metadata_underdeclares(tmp_path: Path) -> None:
    """METADATA declares no workspace members; the live pyproject still does."""
    from _helpers.editable_venv import (
        build_editable_venv,
        imports,
        make_metadata_predate_workspace_split,
    )
    from _helpers.venv import network_available, venv_unavailable

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build an editable venv")

    try:
        venv_python, _checkout = build_editable_venv(tmp_path, REPO_ROOT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        venv_unavailable(f"editable venv build failed/timed out: {exc}")

    modules = make_metadata_predate_workspace_split(venv_python)
    assert not imports(venv_python, "ai_hats_wt"), "setup failed — ai_hats_wt still importable"

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(venv_python), "-m", "ai_hats", "--version"],
        cwd=str(tmp_path),  # neutral cwd: no project config to resolve
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )

    assert result.returncode == 0, (
        f"gate did not heal the under-declared install (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "missing runtime deps" in result.stderr, (
        f"gate never reported the gap it was supposed to close\nstderr:\n{result.stderr}"
    )
    for module in modules:
        assert imports(venv_python, module), f"{module} still unimportable after the heal"
