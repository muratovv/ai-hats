"""E2E: ``ai-hats config status`` prints install diagnostics (HATS-497).

Verifies the new Health-section install fields appear in real launcher
output. The fields are gathered by ``_gather_install_info`` in
``src/ai_hats/cli/maintenance.py`` and rendered from
``src/ai_hats/cli/assembly.py:status()``.

Two sub-cases amortize the heavy bootstrap setup:

  1. **Role-less project.** Fresh project dir with no ``ai-hats.yaml``.
     ``config status`` prints ``No role active`` AND the install Health
     fields (HATS-497 refactor — install info no longer gated on the
     early role-check return).
  2. **Role-initialized project.** After ``ai-hats self init -p claude
     -r assistant``, ``config status`` prints role + composition tree
     + install Health fields + existing project-side checks
     (imports.md, system_prompt with OK/Missing icons).

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Fail-under-revert: with the install-info block removed from
``assembly.py:status()``, the ``Version:`` substring is absent from
both sub-case outputs and the assertion fails. With the role-check
return restored, sub-case 1 misses install fields too.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=None, check=False):
    """Run subprocess; optionally assert exit code."""
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{cmd} exit {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_config_status_install_diagnostics(tmp_path: Path) -> None:
    """End-to-end: install Health fields appear with and without an active role.

    Both sub-cases amortize the ~60s bootstrap (launcher install + pip
    install ai-hats from local repo).
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)
    # PYTHONPATH from the test runner can shadow the venv install by
    # adding the worktree's ``src/`` to sys.path ahead of site-packages.
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)],
         cwd=tmp_path, env=env, timeout=30, check=True)
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=240, check=True)

    # ----- sub-case 1: role-less project -----
    sc1 = _run([str(launcher_dest), "config", "status"],
               cwd=project, env=env, timeout=30, expect_exit=0)
    out1 = sc1.stdout + sc1.stderr
    assert "No role active" in out1, (
        f"role-less project should announce missing role:\n{out1}"
    )
    # Install Health fields MUST be present even without a role.
    for key in ("Version:", "Interpreter:", "Venv:", "Source:",
                "Library:", "Resolved via:"):
        assert key in out1, (
            f"install Health field {key!r} missing from role-less output:\n{out1}"
        )

    # ----- sub-case 2: role-initialized project -----
    _run([str(launcher_dest), "self", "init",
          "-p", "claude", "-r", "assistant"],
         cwd=project, env=env, timeout=60, check=True)

    sc2 = _run([str(launcher_dest), "config", "status"],
               cwd=project, env=env, timeout=30, expect_exit=0)
    out2 = sc2.stdout + sc2.stderr
    assert "Role:" in out2 and "assistant" in out2, (
        f"role section missing from initialized output:\n{out2}"
    )
    # Install Health fields again — same set as sub-case 1.
    for key in ("Version:", "Interpreter:", "Venv:", "Source:",
                "Library:", "Resolved via:"):
        assert key in out2, (
            f"install Health field {key!r} missing from role-init output:\n{out2}"
        )
    # Project-side checks (existing, pre-HATS-497) still present.
    assert "system_prompt:" in out2, (
        f"existing project health check 'system_prompt' missing:\n{out2}"
    )
