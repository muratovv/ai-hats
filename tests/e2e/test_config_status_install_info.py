"""e2e (HATS-497, HATS-582, HATS-707, HATS-1238)

flow:   a user checks installation diagnostics using ai-hats config status in uninitialized and initialized projects
cmds:
    ai-hats config status
expect: installation health fields (version, interpreter, venv, source, library, resolved path) are rendered in both role-less and initialized projects without dead hook branches
why:    installation health diagnostics must be visible regardless of project initialization state so users can troubleshoot setup issues
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=None, check=False):
    """Run subprocess; optionally assert exit code."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{cmd} exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_config_status_install_diagnostics(shared_launcher, tmp_path: Path) -> None:
    """End-to-end: install Health fields appear with and without an active role.

    HATS-582: reuses the session-shared venv (no per-test launcher install +
    self update). Read-only on the venv — only ``config status`` / ``self
    init`` against a fresh ``tmp_path`` project.
    """
    launcher_dest, base_env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    # Copy the session-shared env before mutating it.
    env = dict(base_env)
    # PYTHONPATH from the test runner can shadow the venv install by
    # adding the worktree's ``src/`` to sys.path ahead of site-packages.
    env.pop("PYTHONPATH", None)

    # ----- sub-case 1: role-less project -----
    sc1 = _run(
        [str(launcher_dest), "config", "status"], cwd=project, env=env, timeout=30, expect_exit=0
    )
    out1 = sc1.stdout + sc1.stderr
    assert "No role active" in out1, f"role-less project should announce missing role:\n{out1}"
    # Install Health fields MUST be present even without a role.
    for key in ("Version:", "Interpreter:", "Venv:", "Source:", "Library:", "Resolved via:"):
        assert key in out1, f"install Health field {key!r} missing from role-less output:\n{out1}"

    # ----- sub-case 2: role-initialized project -----
    _run(
        [str(launcher_dest), "self", "init", "-p", "claude", "-r", "assistant"],
        cwd=project,
        env=env,
        timeout=60,
        check=True,
    )

    sc2 = _run(
        [str(launcher_dest), "config", "status"], cwd=project, env=env, timeout=30, expect_exit=0
    )
    out2 = sc2.stdout + sc2.stderr
    assert "Role:" in out2 and "assistant" in out2, (
        f"role section missing from initialized output:\n{out2}"
    )
    # Install Health fields again — same set as sub-case 1.
    for key in ("Version:", "Interpreter:", "Venv:", "Source:", "Library:", "Resolved via:"):
        assert key in out2, f"install Health field {key!r} missing from role-init output:\n{out2}"
    # HATS-1238: Claude provider uses per-session prompt cache; no root prompt file is managed.
    assert "system_prompt:" not in out2, (
        f"claude provider should not report root system_prompt health (HATS-1238):\n{out2}"
    )
    # HATS-707: the dead lifecycle ``hooks:`` channel is gone — the composition
    # tree must NOT render a hooks branch. Pre-HATS-707 the ``assistant`` role
    # declared ``task_complete: [git status]``, which surfaced here as a
    # ``task_complete: [...]`` tree node. Fail-under-revert: restoring the
    # channel (assembler payload + assembly.py renderer) brings it back.
    assert "task_complete" not in out2, (
        f"config status still renders a dead lifecycle-hook branch (HATS-707):\n{out2}"
    )
