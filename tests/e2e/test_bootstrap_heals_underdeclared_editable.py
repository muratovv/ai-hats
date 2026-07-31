"""E2E: the startup gate heals an editable install METADATA under-declares (HATS-1368).

Value under test: a 2026-07-30 sweep of 16 consumer venvs found 8 whose editable
ai-hats METADATA predates the workspace split — it declares no first-party deps
at all, while the live code on ``.pth`` imports five of them. The gate read that
METADATA, saw nothing missing, and returned; the process then died importing
``ai_hats.cli`` (whose subcommands import workspace members at module level),
printing a repair command that runs the very CLI that just failed.

Setup (real venv + real uv + real ``python -m ai_hats``, per ``dev_rule_e2e_gate``):
clone the repo, ``uv pip install -e`` it into a fresh venv, then strip every
``Requires-Dist: ai-hats-*`` line from METADATA and delete the members those
lines protected.

Assertion: the next invocation exits 0 — the gate read the checkout's live
pyproject, found the members missing, re-pointed the editable install, and
re-exec'd — and the workspace member imports again.

Fail-under-revert: point ``_declared_requirements`` back at
``importlib.metadata.requires`` and the gate sees an empty dep list, so the run
dies with ``Inconsistent or broken ai-hats installation`` instead of exiting 0.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.install_heavy

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
