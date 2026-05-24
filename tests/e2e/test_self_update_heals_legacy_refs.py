"""E2E: ``ai-hats self bump`` heals stale legacy-path refs in user-managed files (HATS-397).

Covers the production scenario from the proxmox regression: after the v4
layout migration moves ``.agent/hooks/<file>`` → ``<ai_hats_dir>/library/hooks/<file>``,
user-authored references to the old path in ``.claude/settings.json`` (and
markdown docs) must be auto-updated, so the next Bash hook / runbook still
works without manual intervention.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``. Pipeline-integration
and in-process ``CliRunner`` tests do NOT satisfy the gate.
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


def _git(project_dir: Path, *args: str, env: dict[str, str]) -> None:
    """Run a git command in `project_dir`, raising on non-zero exit."""
    subprocess.run(
        ["git", *args], cwd=str(project_dir), env=env,
        check=True, capture_output=True, text=True,
    )


def _seed_legacy_project(project_dir: Path) -> None:
    """Create a project that mimics the proxmox pre-migration layout.

    Drops a legacy hook script under ``.agent/hooks/``, a ``.claude/settings.json``
    pointing at it, and ``CLAUDE.md`` / docs mentioning legacy paths in prose.
    """
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    hooks = project_dir / ".agent" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guard.sh").write_text("#!/bin/sh\necho ok\n")
    (hooks / "guard.sh").chmod(0o755)

    claude_dir = project_dir / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({
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

    (project_dir / "CLAUDE.md").write_text(
        "# Project doc\n\nHook lives at `.agent/hooks/guard.sh`.\n"
        "See backlog at `.agent/backlog/tasks/X-1/plan.md` for context.\n"
    )
    (project_dir / "docs.md").write_text(
        "Retro at `.agent/retrospectives/2026-01-01-foo.md`\n"
    )


def _git_init_commit(project_dir: Path, env: dict[str, str]) -> None:
    _git(project_dir, "init", "-q", env=env)
    _git(project_dir, "config", "user.email", "test@example.com", env=env)
    _git(project_dir, "config", "user.name", "Test", env=env)
    _git(project_dir, "add", "-A", env=env)
    _git(project_dir, "commit", "-q", "-m", "seed", env=env)


@pytest.fixture(scope="module")
def installed_launcher(tmp_path_factory):
    """Install ai-hats once per module — pip install is slow (~60s).

    Bootstraps a single shared venv (under ``_bootstrap_proj/``) and pins
    every test invocation to it via ``AI_HATS_VENV``. Avoids paying the
    ~30s ``self update`` cost in every test fixture.
    """
    tmp = tmp_path_factory.mktemp("launcher")
    launcher_dest = tmp / "bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp, env=env, timeout=30)
    bootstrap_proj = tmp / "_bootstrap_proj"
    bootstrap_proj.mkdir()
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=bootstrap_proj, env=env, timeout=180,
    )
    shared_venv = bootstrap_proj / ".agent" / "ai-hats" / ".venv"
    assert shared_venv.is_dir(), "bootstrap did not create shared venv"
    env["AI_HATS_VENV"] = str(shared_venv)
    return launcher_dest, env


@pytest.mark.integration
def test_e2e_healer_rewrites_settings_and_clean_markdown(installed_launcher, tmp_path):
    """Clean git tree → settings.json + markdown both auto-healed in one bump."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _seed_legacy_project(project)
    _git_init_commit(project, env)

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=120,
    )

    # File migrated to new location
    assert (project / ".agent" / "ai-hats" / "library" / "hooks" / "guard.sh").is_file()
    assert not (project / ".agent" / "hooks").exists()

    # settings.json hook command rewritten
    settings = json.loads(
        (project / ".claude" / "settings.json").read_text()
    )
    cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert ".agent/hooks/" not in cmd, f"hook command not healed: {cmd}"
    assert "library/hooks/guard.sh" in cmd, f"new path missing in: {cmd}"

    # Markdown files rewritten (clean git tree)
    claude_md = (project / "CLAUDE.md").read_text()
    assert ".agent/hooks/" not in claude_md
    assert ".agent/backlog/" not in claude_md
    assert "library/hooks/guard.sh" in claude_md
    assert "tracker/backlog/" in claude_md

    docs_md = (project / "docs.md").read_text()
    assert ".agent/retrospectives/" not in docs_md
    assert "sessions/retros/" in docs_md

    # Heal markers emitted to stderr
    combined = res.stdout + res.stderr
    assert "[heal] Healed:" in combined or "[heal] Auto-healed" in combined


@pytest.mark.integration
def test_e2e_healer_dirty_markdown_falls_back_to_inventory(installed_launcher, tmp_path):
    """Markdown with uncommitted changes is preserved + listed in inventory."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _seed_legacy_project(project)
    _git_init_commit(project, env)
    # Make CLAUDE.md dirty after commit
    (project / "CLAUDE.md").write_text(
        "# Project doc\n\nHook lives at `.agent/hooks/guard.sh`.\n"
        "UNCOMMITTED EDIT\n"
    )

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=120,
    )

    # CLAUDE.md NOT auto-rewritten (dirty)
    claude_md = (project / "CLAUDE.md").read_text()
    assert ".agent/hooks/guard.sh" in claude_md, \
        f"dirty CLAUDE.md was modified despite git-dirty gate: {claude_md!r}"
    assert "UNCOMMITTED EDIT" in claude_md

    # Inventory audit-log written
    audits = project / ".agent" / "ai-hats" / "sessions" / "audits"
    audit_files = list(audits.glob("*-legacy-refs.md")) if audits.exists() else []
    assert audit_files, "expected inventory audit file under sessions/audits/"
    inventory_content = audit_files[0].read_text()
    assert "CLAUDE.md" in inventory_content
    assert ".agent/hooks/" in inventory_content

    # Manual-fixes banner present
    combined = res.stdout + res.stderr
    assert "Manual fixes required" in combined or "[heal]" in combined


@pytest.mark.integration
def test_e2e_healer_idempotent_rerun(installed_launcher, tmp_path):
    """Second bump on healed clean tree → no further changes, no new inventory."""
    launcher, env = installed_launcher
    project = tmp_path / "proj"
    project.mkdir()
    _seed_legacy_project(project)
    _git_init_commit(project, env)

    _run([f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal"], cwd=project, env=env, timeout=120)
    # Commit the heal so the tree is clean again
    _git(project, "add", "-A", env=env)
    _git(project, "commit", "-q", "-m", "post-heal", env=env)

    audits = project / ".agent" / "ai-hats" / "sessions" / "audits"
    before = {p.name for p in audits.glob("*")} if audits.exists() else set()

    res = _run(
        [f"{env["AI_HATS_VENV"]}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=120,
    )

    after = {p.name for p in audits.glob("*")} if audits.exists() else set()
    assert before == after, \
        f"second bump created inventory artefacts (before={before} after={after})"

    # No new "Healed:" lines
    combined = res.stdout + res.stderr
    assert "[heal] Healed:" not in combined
