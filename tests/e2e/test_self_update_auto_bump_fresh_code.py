"""E2E: ``ai-hats self update`` repairs legacy refs in a single invocation (HATS-400).

The proxmox regression that motivated this test:

  1. User had ``.claude/settings.json`` pointing at ``.agent/hooks/<file>``.
  2. User ran ``ai-hats self update``: pip install pulled new code (with
     migration_healer), but the in-process auto-bump kept using the OLD
     in-memory code (no healer). Project remained half-fixed.
  3. User had to run ``ai-hats self bump`` a second time manually.

After HATS-400, ``ai-hats self update`` re-execs auto-bump in a fresh
subprocess when the version changed → newly installed code (healer,
migrations) activates immediately.

This test seeds a project that looks like proxmox pre-fix, runs ONE
``ai-hats self update``, and asserts the legacy refs are healed without a
second invocation.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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
def test_e2e_self_update_heals_legacy_in_one_pass(tmp_path: Path) -> None:
    """``ai-hats self update`` (single call) heals legacy refs.

    This validates the proxmox-equivalent flow end-to-end. Whether the bump
    sub-step runs in-process or as a fresh subprocess depends on whether
    pip detected a version change; either path must leave the project's
    ``.claude/settings.json`` healed by exit.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)

    # First self update: bootstrap pulls the current ai-hats into the
    # project venv. No legacy refs yet — just establish the install.
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=180,
    )
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=60,
    )

    # Seed a proxmox-style legacy ref: hook file at the legacy path +
    # .claude/settings.json command pointing to it.
    legacy_hooks = project / ".agent" / "hooks"
    legacy_hooks.mkdir(parents=True)
    (legacy_hooks / "guard.sh").write_text("#!/bin/sh\necho ok\n")
    (legacy_hooks / "guard.sh").chmod(0o755)

    settings = project / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": "$CLAUDE_PROJECT_DIR/.agent/hooks/guard.sh",
                }],
            }],
        },
    }, indent=2) + "\n")

    # Initialize git so the healer's git-clean gate can evaluate cleanliness.
    subprocess.run(
        ["git", "init", "-q"], cwd=str(project), env=env, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(project), env=env, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(project), env=env, check=True,
    )
    subprocess.run(
        ["git", "add", "-A"], cwd=str(project), env=env, check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=str(project), env=env, check=True,
    )

    # The actual test: ONE invocation of self update should fix everything.
    res = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=180,
    )

    # File migrated under the managed namespace.
    assert (project / ".agent" / "ai-hats" / "library" / "hooks" / "guard.sh").is_file(), \
        f"hook not migrated. stdout:\n{res.stdout}\nstderr:\n{res.stderr}"

    # settings.json command rewritten to new path — the proxmox-fix smoke.
    settings_data = json.loads(settings.read_text())
    cmd = settings_data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert ".agent/hooks/" not in cmd, \
        f"settings.json hook command not healed in single update: {cmd!r}"
    assert "library/hooks/guard.sh" in cmd, \
        f"new path missing from healed command: {cmd!r}"


@pytest.mark.integration
def test_e2e_python_dash_m_ai_hats_self_bump_invokable(tmp_path: Path) -> None:
    """``python -m ai_hats self bump`` works — the subprocess form HATS-400 uses.

    Defensive: validates the entry-point contract that update() depends on.
    A regression here (broken ``__main__``, missing CLI nesting) would
    silently break the fresh-interpreter bump.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=180,
    )
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=60,
    )

    # Locate project's python interpreter inside its ai-hats venv
    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert venv_python.is_file(), "project venv python missing"

    res = _run(
        [str(venv_python), "-m", "ai_hats", "self", "bump"],
        cwd=project, env=env, timeout=60,
    )
    # Successful bump produces a 'Bumped:' line (per Assembler.bump output flow).
    combined = res.stdout + res.stderr
    assert "Bumped:" in combined or "No composition changes" in combined or "[heal]" in combined, \
        f"unexpected output from python -m ai_hats self bump:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
