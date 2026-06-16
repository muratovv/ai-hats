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

from _helpers.project import pin_edge_channel


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.repo_src import build_src  # noqa: E402

pytestmark = pytest.mark.pip_heavy  # HATS-678: real pip at call time → capped via conftest.PIP_HEAVY_GROUPS


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
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(build_src(REPO_ROOT))
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)

    # First self update: bootstrap pulls the current ai-hats into the
    # project venv. No legacy refs yet — just establish the install.
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=300,  # HATS-675: 300s = -n8 gate suite norm
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
    # HATS-294 dropped the permanent ``.claude/skills`` export; the
    # assembler's legacy-claude cleanup may rmdir an empty ``.claude/``
    # during the test's init phase, so the parent isn't guaranteed to
    # exist by the time we write settings.json. Create it explicitly.
    settings.parent.mkdir(parents=True, exist_ok=True)
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

    # HATS-469: ``heal_external_refs`` is registry step 4 (one-shot gated
    # by ``migration_step``). Post-HATS-469 ``ai-hats self init`` seeds
    # ``migration_step=latest``, so the next bump would skip the heal.
    # Rewind below step 4 so the subsequent ``self update`` actually
    # replays the heal entry against our planted legacy file.
    import yaml as _yaml
    cfg_path = project / "ai-hats.yaml"
    cfg_data = _yaml.safe_load(cfg_path.read_text())
    cfg_data["migration_step"] = 3
    cfg_data["harness"] = {"channel": "edge"}  # HATS-764: edge for the 2nd update
    cfg_path.write_text(_yaml.safe_dump(cfg_data))

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
        cwd=project, env=env, timeout=300,  # HATS-675: 300s = -n8 gate suite norm
    )

    # HATS-549 Phase 4: ``guard.sh`` is user-owned (basename NOT in
    # the ai-hats whitelist) — partition routes it to user-hooks/,
    # healer Phase 4 pre-pass disables the settings.json entry.
    assert (project / ".agent" / "ai-hats" / "user-hooks" / "guard.sh").is_file(), \
        f"hook not relocated to user-hooks/. stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert not (project / ".agent" / "ai-hats" / "library" / "hooks" / "guard.sh").exists(), \
        "user-owned hook must not land in managed library/hooks/ namespace"

    # settings.json no longer carries the user-owned hook entry —
    # disable behavior (explicit re-enable required).
    raw_settings = settings.read_text()
    assert ".agent/hooks/guard.sh" not in raw_settings
    assert "library/hooks/guard.sh" not in raw_settings
    assert "user-hooks/guard.sh" not in raw_settings, \
        "Phase 4 disables; entry must be REMOVED, not auto-rewritten"


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
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(build_src(REPO_ROOT))
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=300,  # HATS-675: 300s = -n8 gate suite norm
    )
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=60,
    )

    # Locate project's python interpreter inside its ai-hats venv
    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert venv_python.is_file(), "project venv python missing"

    res = _run(
        [str(venv_python), "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=60,
    )
    # Successful bump produces a 'Bumped:' line (per Assembler.bump output flow).
    combined = res.stdout + res.stderr
    assert "Bumped:" in combined or "No composition changes" in combined or "[heal]" in combined, \
        f"unexpected output from python -m ai_hats self bump:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
